import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

try:
    import requests
    from tqdm import tqdm
except ImportError:
    print("Missing packages. Run:")
    print("pip install requests tqdm")
    sys.exit(1)

URL = "https://archive.recagain.site/download/2023-04-18T06-58-58Z"

BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_DIR = BASE_DIR / "Build"
ARCHIVE_FILE = BASE_DIR / "RecRoom-2023-04-18.zip"

BEPINEX_URL = (
    "https://github.com/BepInEx/BepInEx/releases/download/v6.0.0-pre.2/"
    "BepInEx-Unity.IL2CPP-win-x64-6.0.0-pre.2.zip"
)
BEPINEX_ARCHIVE = BASE_DIR / "BepInEx-IL2CPP-6.0.0-pre.2.zip"

CLIENT_URL = "https://github.com/recadoodle/client/archive/refs/heads/main.zip"
CLIENT_ARCHIVE = BASE_DIR / "recadoodle-client.zip"
CLIENT_DIR = BASE_DIR.parent / "client"
CLIENT_BUILD_DIR = CLIENT_DIR / "builds"

DEFAULT_NAMESERVER = "http://127.0.0.1:5000"
INTEROP_TIMEOUT = 15 * 60


def clear():
    os.system("cls" if os.name == "nt" else "clear")


def banner():
    print(r"""
══════════════════════════════════════════════════════════
               RECADOODLE BUILD DOWNLOADER              
                     liamcodeslol :)                                                                   
══════════════════════════════════════════════════════════
""")


def download(url=URL, target=ARCHIVE_FILE, label="Rec Room"):
    print(f"\nConnecting to download server ({label})")

    response = requests.get(
        url,
        stream=True,
        timeout=30
    )

    response.raise_for_status()

    total = int(response.headers.get("content-length", 0))

    print("Starting Download...\n")

    start_time = time.time()

    with open(target, "wb") as file:
        with tqdm(
            total=total,
            unit="B",
            unit_scale=True,
            unit_divisor=1024,
            desc=label,
            ncols=90,
            bar_format=(
                "{l_bar}{bar}| "
                "{percentage:3.0f}% "
                "{n_fmt}/{total_fmt} "
                "[{elapsed}<{remaining}, {rate_fmt}]"
            )
        ) as progress:

            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    file.write(chunk)
                    progress.update(len(chunk))

    elapsed = time.time() - start_time

    print(f"\nDownload finished in {elapsed:.1f} seconds.")


def extract():
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    print("\nExtracting build...")
    print(f"Destination: {DOWNLOAD_DIR}\n")

    with zipfile.ZipFile(ARCHIVE_FILE, "r") as archive:
        members = archive.infolist()

        with tqdm(
            total=len(members),
            desc="Extracting",
            ncols=90,
            unit="files"
        ) as progress:

            for member in members:
                archive.extract(member, DOWNLOAD_DIR)
                progress.update(1)

    ARCHIVE_FILE.unlink(missing_ok=True)

    print("\nExtraction complete.")


def unzip(archive_file, destination):
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_file, "r") as archive:
        archive.extractall(destination)
    archive_file.unlink(missing_ok=True)


def find_game_dir():
    exe = next(DOWNLOAD_DIR.rglob("Recroom_Release.exe"), None) or next(
        (p for p in DOWNLOAD_DIR.rglob("*.exe")
         if (p.parent / "GameAssembly.dll").exists()),
        None,
    )
    if exe is None:
        raise RuntimeError(f"Could not find the Rec Room executable in {DOWNLOAD_DIR}")
    return exe


def install_bepinex(game_dir):
    if (game_dir / "BepInEx" / "core").exists():
        print("\nBepInEx already installed, skipping.")
        return
    download(BEPINEX_URL, BEPINEX_ARCHIVE, "BepInEx 6 pre.2")
    print("Installing BepInEx...")
    unzip(BEPINEX_ARCHIVE, game_dir)


def generate_interop(game_exe):
    game_dir = game_exe.parent
    marker = game_dir / "BepInEx" / "interop" / "Assembly-CSharp.dll"
    if marker.exists():
        print("\nBepInEx interop assemblies already generated, skipping.")
        return

    log_file = game_dir / "BepInEx" / "LogOutput.log"
    print("\nLaunching Rec Room once so BepInEx can generate interop assemblies.")
    print("This can take several minutes. The game will be closed automatically.")
    process = subprocess.Popen([str(game_exe)], cwd=game_dir)

    deadline = time.time() + INTEROP_TIMEOUT
    try:
        while time.time() < deadline:
            log = log_file.read_text(errors="ignore") if log_file.exists() else ""
            if marker.exists() and "Chainloader" in log:
                time.sleep(5)
                return
            if process.poll() is not None:
                break
            time.sleep(3)
    finally:
        if process.poll() is None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                           capture_output=True)

    if not marker.exists():
        raise RuntimeError(
            "BepInEx did not generate interop assemblies. "
            f"Check {log_file} for details."
        )


def build_client(game_dir):
    if shutil.which("dotnet") is None:
        raise RuntimeError("The .NET SDK is required: https://dotnet.microsoft.com/download")

    download(CLIENT_URL, CLIENT_ARCHIVE, "Recadoodle client")
    staging = BASE_DIR / "_client_src"
    shutil.rmtree(staging, ignore_errors=True)
    unzip(CLIENT_ARCHIVE, staging)
    source = next(staging.iterdir())

    # Keep previous build outputs, refresh everything else.
    CLIENT_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, CLIENT_DIR, dirs_exist_ok=True)
    shutil.rmtree(staging, ignore_errors=True)

    print("\nBuilding Recadoodle client patch...")
    result = subprocess.run(
        ["dotnet", "build", str(CLIENT_DIR / "Recadoodle.csproj"),
         "-c", "Release", f"-p:GamePath={game_dir}", "-o", str(CLIENT_BUILD_DIR)],
        cwd=CLIENT_DIR,
    )
    if result.returncode != 0:
        raise RuntimeError("dotnet build failed (see output above).")

    plugins = game_dir / "BepInEx" / "plugins"
    plugins.mkdir(parents=True, exist_ok=True)
    shutil.copy2(CLIENT_BUILD_DIR / "Recadoodle.dll", plugins / "Recadoodle.dll")
    print(f"Patch built to {CLIENT_BUILD_DIR} and installed to {plugins}")


def ask_nameserver():
    nameserver = input(
        f"\nRecadoodle nameserver [{DEFAULT_NAMESERVER}]: "
    ).strip() or DEFAULT_NAMESERVER
    if "://" not in nameserver:
        nameserver = "http://" + nameserver
    return nameserver.rstrip("/")


PHOTON_KEYS = {
    "App Id Realtime": "REPLACE_WITH_YOUR_REALTIME_APP_ID",
    "App Id Voice": "REPLACE_WITH_YOUR_VOICE_APP_ID",
    "App Id Chat": "REPLACE_WITH_YOUR_CHAT_APP_ID",
}


def ask_photon_ids():
    print("\nPhoton app IDs (from dashboard.photonengine.com). Leave blank to skip.")
    ids = {}
    for key in PHOTON_KEYS:
        while True:
            value = input(f"  Photon {key}: ").strip()
            if not value or re.fullmatch(r"[0-9a-fA-F-]{32,36}", value):
                break
            print("  That doesn't look like a Photon app ID (expected a GUID).")
        ids[key] = value
    return ids


def write_config(game_dir, nameserver, photon_ids):

    template = CLIENT_DIR / "recadoodle.patch.cfg.example"
    config = template.read_text(encoding="utf-8")
    config = config.replace(
        f"RecNet NameServer Host = {DEFAULT_NAMESERVER}",
        f"RecNet NameServer Host = {nameserver}",
    )
    for key, placeholder in PHOTON_KEYS.items():
        config = config.replace(
            f"{key} = {placeholder}", f"{key} = {photon_ids.get(key, '')}"
        )

    config_path = game_dir / "BepInEx" / "config" / "recadoodle.patch.cfg"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(config, encoding="utf-8")
    print(f"Nameserver set to {nameserver}")
    if all(photon_ids.values()):
        print("Photon app IDs saved.")
    else:
        print(f"Some Photon app IDs were skipped; add them in {config_path}")


def patch(nameserver, photon_ids):
    game_exe = find_game_dir()
    game_dir = game_exe.parent
    install_bepinex(game_dir)
    generate_interop(game_exe)
    build_client(game_dir)
    write_config(game_dir, nameserver, photon_ids)


def main():
    clear()
    banner()

    print("\nYou must own Rec Room or have it in your library on Steam to use the build.")
    answer = input("Do you own Rec Room on Steam? [Y/N]: ").strip().lower()

    if answer not in ("y", "yes"):
        print("\nidk how to bypass that uhh.")
        input("\nPress Enter to exit bye...")
        return

    print("\n ok.")
    nameserver = ask_nameserver()
    photon_ids = ask_photon_ids()

    try:
        if not any(DOWNLOAD_DIR.rglob("GameAssembly.dll")):
            download()
            extract()
        else:
            print("\nBuild already downloaded, skipping download.")
        patch(nameserver, photon_ids)

    except requests.RequestException as error:
        print("\nthe download failed check ur internet")
        print(f"    {error}")
        input("\nPress Enter to exit bye...")
        return

    except zipfile.BadZipFile:
        print("\nerror with that zip yk")
        input("\nPress Enter to exit bye...")
        return

    except Exception as error:
        print("\n we dont EXACTLY know what happened it just did tho.")
        print(f"    {error}")
        input("\nPress Enter to exit bye...")
        return

    print("""
╔══════════════════════════════════════════════════════════╗
║                                                          ║
║                       SUCCESS                            ║
║                                                          ║
║       Build downloaded and patched!                      ║
║                                                          ║
╚══════════════════════════════════════════════════════════╝
""")

    input("\nPress Enter to exit...")


if __name__ == "__main__":
    main()
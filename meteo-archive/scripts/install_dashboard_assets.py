"""Install pinned browser bundles into Django's static directory."""
import argparse
import base64
import hashlib
from io import BytesIO
from pathlib import Path
from tarfile import open as open_tar
from urllib.request import urlopen


BUNDLES = (
    ("echarts", "6.1.0", "q0yaFPggC9FUdsWH4blavRWFmxdrIodbkoKNAjJudAI6CA9gNPxHtV2RcZNEepZVlk4yvBYkOkbk6HIVpIyHZA==",
     ("dist/echarts.min.js", "LICENSE", "NOTICE")),
    ("maplibre-gl", "5.24.0", "ALyFxgtd5R+65UqZ/++lOqwWcC0SNho9c27fYSyLmG7AfnAul2o46F05aDJGPbFU57wos9dgcIySHs0Xe6ia3A==",
     ("dist/maplibre-gl.js", "dist/maplibre-gl.css", "LICENSE.txt")),
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=Path("catalog/static/catalog/vendor"))
    destination = parser.parse_args().destination
    files = {}
    for name, version, checksum, members in BUNDLES:
        url = f"https://registry.npmjs.org/{name}/-/{name}-{version}.tgz"
        with urlopen(url, timeout=40) as response:
            content = response.read(40_000_001)
        if len(content) > 40_000_000 or hashlib.sha512(content).digest() != base64.b64decode(checksum):
            raise SystemExit(f"Checksum mismatch for {name}@{version}")
        with open_tar(fileobj=BytesIO(content), mode="r:gz") as bundle:
            for member in members:
                filename = Path(member).name
                if not member.startswith("dist/"):
                    filename = f"{name}-{filename}"
                with bundle.extractfile(f"package/{member}") as source:
                    data = source.read()
                if filename.endswith(".js"):
                    data = data.rsplit(b"\n//# sourceMappingURL=", maxsplit=1)[0]
                files[filename] = data
    destination.mkdir(parents=True, exist_ok=True)
    for filename, data in files.items():
        (destination / filename).write_bytes(data)


if __name__ == "__main__":
    main()

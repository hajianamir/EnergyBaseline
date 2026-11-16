"""Pretrained model files provisioned by explicit operator action and verified on every use.

Weights are never downloaded while an analysis runs. ``install_asset`` either copies files
from a local directory or fetches the fixed HTTPS sources below; both paths accept a file
only when its byte count and SHA-256 match the values recorded here. Hash verification
binds content, it is not an operating-system protection of the installed directory.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
from types import MappingProxyType

from .isolated import ProvisioningRequired


@dataclass(frozen=True)
class AssetFile:
    name: str
    size: int
    sha256: str
    url: str


@dataclass(frozen=True)
class Asset:
    name: str
    license: str
    origin: str
    files: tuple[AssetFile, ...]


_GHOSTNET = "https://raw.githubusercontent.com/huawei-noah/Efficient-AI-Backbones/f90e129b645c3b1684fe07cd361cd557d0ad71f7"
_HUB = "https://raw.githubusercontent.com/pytorch/hub/c3beaae7d32fca2a23fec30aa7938ef5c9b6e5d5"
_CHRONOS = "https://huggingface.co/amazon/chronos-bolt-tiny/resolve/a0e552de83495b5c28c14c71c374f3e33280b340"

ASSETS = MappingProxyType(
    {
        "chronos-bolt-tiny": Asset(
            "chronos-bolt-tiny",
            "Apache-2.0",
            "amazon/chronos-bolt-tiny at revision a0e552de83495b5c28c14c71c374f3e33280b340: "
            "zero-shot forecasting weights, about 9 million parameters.",
            (
                AssetFile(
                    "config.json", 1120,
                    "278f0086733031635fb1c861cb01c1bad6477420c7fcb19381a2993e335785e0",
                    f"{_CHRONOS}/config.json",
                ),
                AssetFile(
                    "model.safetensors", 34622352,
                    "75068728d376d2bec670379eeef4bfb4d24c0cfe24d957451f8d19b447030a32",
                    f"{_CHRONOS}/model.safetensors",
                ),
            ),  # fmt: skip
        ),
        "ghostnet-imagenet": Asset(
            "ghostnet-imagenet",
            "Apache-2.0 for the weights (trained on ImageNet-1k; observe the ImageNet terms of access). "
            "The class-name list comes from pytorch/hub, which publishes no licence file.",
            "Huawei Noah's Ark Lab GhostNet 1.0x, published ImageNet-1k top-1 accuracy 73.98%.",
            (
                AssetFile(
                    "state_dict_73.98.pth", 20923722,
                    "965143b0cfbb704283890f95f0037be632d8fa095047e3a2c9e63ed0a9a1df14",
                    f"{_GHOSTNET}/ghostnet_pytorch/models/state_dict_73.98.pth",
                ),
                AssetFile(
                    "imagenet_classes.txt", 10472,
                    "1f386e0d1cb6e28b9c2dac651c3dea6801e98ad1b41a14ce6bb1a093d72069f5",
                    f"{_HUB}/imagenet_classes.txt",
                ),
            ),  # fmt: skip
        ),
    }
)


def _asset(name):
    if not isinstance(name, str) or name not in ASSETS:
        raise ValueError("Unknown model asset.")
    return ASSETS[name]


def assets_root():
    from .io.paths import resolve_app_home

    return resolve_app_home()[0] / "model-assets"


def _matches(path, item):
    """Check size first, then hash the whole file in bounded chunks."""
    try:
        if not path.is_file() or path.is_symlink() or path.stat().st_size != item.size:
            return False
        digest = hashlib.sha256()
        with open(path, "rb") as stream:
            for block in iter(lambda: stream.read(1 << 20), b""):
                digest.update(block)
        return digest.hexdigest() == item.sha256
    except OSError:
        return False


def _verified(asset, root):
    """Every approved file matches and the directory holds nothing else a loader could pick up."""
    try:
        if root.is_symlink() or sorted(os.listdir(root)) != sorted(item.name for item in asset.files):
            return False
    except OSError:
        return False
    return all(_matches(root / item.name, item) for item in asset.files)


def asset_status(name):
    asset = _asset(name)
    root = assets_root() / asset.name
    if _verified(asset, root):
        state = "installed"
    else:
        state = "invalid" if root.exists() else "not-installed"
    return {
        "asset": asset.name,
        "license": asset.license,
        "origin": asset.origin,
        "files": [{"name": f.name, "bytes": f.size, "sha256": f.sha256, "source": f.url} for f in asset.files],
        "state": state,
    }


def verified_asset(name):
    """Return the asset directory after re-hashing every file, or explain how to provision it."""
    asset = _asset(name)
    root = assets_root() / asset.name
    if not _verified(asset, root):
        raise ProvisioningRequired(
            f"Model asset '{asset.name}' is absent or does not match its approved hashes; "
            f"provision it with 'assets install {asset.name}'."
        )
    return root


def _download(item, target):
    """Fetch one fixed HTTPS source, refusing downgrades and anything beyond the expected size."""
    import urllib.error
    import urllib.request

    class HttpsOnly(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if not newurl.lower().startswith("https://"):
                raise urllib.error.HTTPError(newurl, code, "Redirect left HTTPS.", headers, fp)
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    opener = urllib.request.build_opener(HttpsOnly)
    request = urllib.request.Request(item.url, headers={"User-Agent": "EnergyBaseline-asset-provisioning"})
    received = 0
    with opener.open(request, timeout=120) as reply, open(target, "wb") as stream:  # noqa: S310 - fixed HTTPS URL
        while True:
            block = reply.read(1 << 20)
            if not block:
                break
            received += len(block)
            if received > item.size:
                raise ProvisioningRequired("Model asset source returned more bytes than approved.")
            stream.write(block)


def install_asset(name, source=None):
    """Provision an asset from ``source`` (a local directory) or from its fixed HTTPS origins.

    Files are staged privately, verified, then moved into place; a mismatch installs nothing.
    """
    asset = _asset(name)
    root = assets_root() / asset.name
    root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f"{asset.name}-", dir=root.parent))
    try:
        for item in asset.files:
            target = staging / item.name
            if source is not None:
                candidate = Path(source) / item.name
                if not candidate.is_file():
                    raise ProvisioningRequired(f"Local asset source lacks {item.name}.")
                shutil.copyfile(candidate, target)
            else:
                try:
                    _download(item, target)
                except OSError as exc:
                    raise ProvisioningRequired(
                        f"Model asset download failed ({type(exc).__name__}); retry or use a local source directory."
                    ) from None
            if not _matches(target, item):
                raise ProvisioningRequired(f"{item.name} does not match its approved size and SHA-256.")
        shutil.rmtree(root, ignore_errors=True)
        os.replace(staging, root)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return asset_status(name)


def remove_asset(name):
    shutil.rmtree(assets_root() / _asset(name).name, ignore_errors=True)
    return asset_status(name)

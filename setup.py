#!/usr/bin/python3
import sys

from setuptools import setup
from setuptools_rust import Binding, RustExtension, RustBin

features = []

if sys.platform == "linux":
    # TODO: Check if the "debian" extra is needed
    features.append("debian")

rust_extensions = [
    RustExtension(
        "silver_platter",
        "svp-py/Cargo.toml",
        binding=Binding.PyO3,
        args=["--no-default-features"],
        features=features + ["extension-module"],
    ),
    RustBin(
        "svp",
        "Cargo.toml",
        features=features,
    )
]

if "debian" in features:
    rust_extensions.append(
        RustBin(
            "debian-svp",
            "Cargo.toml",
            features=features + ["debian"],
        )
    )

setup(rust_extensions=rust_extensions)

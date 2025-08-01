#!/usr/bin/python3
import sys

from setuptools import setup
from setuptools_rust import Binding, RustExtension

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
]

setup(rust_extensions=rust_extensions)

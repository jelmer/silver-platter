#!/usr/bin/python3
import sys

from setuptools import setup
from setuptools_rust import Binding, RustExtension

features = []

if sys.platform == "linux":
    features.append("debian")

setup(
    rust_extensions=[
        RustExtension(
            "silver_platter._svp_rs",
            "crates/svp-py/Cargo.toml",
            binding=Binding.PyO3,
            args=["--no-default-features"],
            features=features,
        )
    ],
)

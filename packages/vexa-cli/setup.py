from setuptools import setup, find_packages
setup(
    name="vexa-cli",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "httpx>=0.27",
        "rich>=13.0",
        "click>=8.0",
        "prompt_toolkit>=3.0",
    ],
    entry_points={
        "console_scripts": [
            "vexa=vexa_cli.main:cli",
        ],
    },
    python_requires=">=3.9",
)

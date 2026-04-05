from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="vexa-client",
    version="0.5.4",
    author="Vexa Team",
    author_email="support@vexa.ai",
    description="Python client library for Vexa - privacy-first, open-source API for real-time meeting transcription",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/Vexa-ai/vexa",
    project_urls={
        "Documentation": "https://github.com/Vexa-ai/vexa/blob/main/docs/user_api_guide.md",
        "Bug Tracker": "https://github.com/Vexa-ai/vexa/issues",
        "Discord": "https://discord.com/invite/Ga9duGkVz9",
        "Website": "https://www.vexa.ai",
    },
    packages=find_packages(),
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Topic :: Communications :: Chat",
        "Topic :: Multimedia :: Sound/Audio :: Speech",
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
    ],
    python_requires=">=3.7",
    install_requires=[
        "requests>=2.25.0",
    ],
    keywords="meeting transcription translation real-time api client vexa",
    license="MIT",
) 
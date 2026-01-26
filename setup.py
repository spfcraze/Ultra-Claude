from setuptools import setup, find_packages

setup(
    name="ultraclaude",
    version="0.4.0",
    packages=find_packages(),
    include_package_data=True,
    install_requires=[
        "fastapi>=0.104.0",
        "uvicorn[standard]>=0.24.0",
        "websockets>=12.0",
        "aiofiles>=23.2.1",
        "python-multipart>=0.0.6",
        "jinja2>=3.1.2",
        "pydantic>=2.5.0",
        "click>=8.1.7",
        "rich>=13.7.0",
        "plyer>=2.1.0",
        "psutil>=5.9.0",
        "requests>=2.31.0",
    ],
    entry_points={
        "console_scripts": [
            "ultraclaude=main:cli",
        ],
    },
    python_requires=">=3.10",
    author="UltraClaude",
    description="Multi-session Claude Code Manager with web dashboard",
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
)

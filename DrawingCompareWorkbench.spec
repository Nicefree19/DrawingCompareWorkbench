# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_all


ROOT = Path(SPECPATH).resolve()
# Audit-gates §11.3 — bundle the scripts/ directory so the subprocess proxy
# (viewer_package_proxy → scripts/render_viewer_package_subprocess.py) can
# locate its child entry point inside the PyInstaller payload.
datas = [
    (str(ROOT / "src"), "src"),
    (str(ROOT / "scripts"), "scripts"),
]
binaries = []
hiddenimports = [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "cv2",
    "ezdxf",
    "fitz",
    "numpy",
    "openpyxl",
    "PIL",
    "PIL.Image",
    "scipy",
    "scipy.optimize",
    "skimage",
    "skimage.metrics",
    # Audit-gates §11.3 — RuntimeBudgetSampler dependency (psutil) plus
    # modules that the subprocess script lazy-imports. PyInstaller does
    # not follow the stdlib subprocess invocation, so without explicit
    # registration these imports fail in the bundled binary.
    "psutil",
    "src.services.comparison.runtime_budget",
    "src.services.comparison.adaptive_quality",
    "src.services.comparison.viewer_package_proxy",
    "src.services.comparison.cad_visual_backend",
    "src.services.comparison.cad_visual_conversion_worker",
    "src.services.comparison.render_backend_registry",
    "src.services.comparison.visual_asset",
]

for package_name in ("ezdxf", "cv2", "fitz", "openpyxl", "PIL", "scipy"):
    try:
        package_datas, package_binaries, package_hiddenimports = collect_all(package_name)
    except Exception:
        continue
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports


a = Analysis(
    [str(ROOT / "start_drawing_compare_workbench.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],  # no custom PyInstaller hooks (hooks/ dir does not exist) — audit BDC-3
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "PyQt4",
        "PyQt5",
        "PyQt6",
        "pytest",
        "tests",
        "sphinx",
        "docutils",
        "boto3",
        "botocore",
        "pyarrow",
        "altair",
        "bitsandbytes",
        "cupy",
        "datasets",
        "easyocr",
        "faiss",
        "faiss_cpu",
        "google.generativeai",
        "gradio",
        "huggingface_hub",
        "langchain",
        "llama_cpp",
        "nltk",
        "paddle",
        "paddleocr",
        "playwright",
        "qdrant_client",
        "ray",
        "selenium",
        "sentence_transformers",
        "spacy",
        "tensorflow",
        "torch",
        "torchaudio",
        "torchvision",
        "transformers",
        "vllm",
        "wandb",
        "xgboost",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DrawingCompareWorkbench",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="DrawingCompareWorkbench",
)

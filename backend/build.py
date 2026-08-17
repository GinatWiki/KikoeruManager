import os
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

APP_NAME = "KikoeruManager"
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
ICON_PATH = os.path.join(BACKEND_DIR, "app.ico")
VERSION_FILE = os.path.join(BACKEND_DIR, "app", "version.txt")


def resolve_app_version():
    version = os.environ.get("KIKOERUMANAGER_VERSION", "").strip()
    if not version:
        try:
            result = subprocess.run(
                ["git", "describe", "--tags", "--abbrev=0", "--match", "v*.*.*"],
                cwd=ROOT_DIR,
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                version = result.stdout.strip()
        except Exception:
            version = ""
    if not version:
        version = "dev"
    if not version.lower().startswith("v") and version != "dev":
        version = "v" + version
    return version[1:] if version.lower().startswith("v") else version

def check_build_dependencies():
    try:
        import pystray  # noqa: F401
        import PIL  # noqa: F401
        import qrcode  # noqa: F401
        return True
    except Exception as e:
        print(f"缺少打包依赖: {e}")
        print("请先执行: pip install -r requirements.txt")
        return False

def build(console_mode=True):
    name = APP_NAME if console_mode else f"{APP_NAME}-noconsole"

    icon_option = [ICON_PATH] if os.path.exists(ICON_PATH) else []
    datas = [('../frontend/dist', 'frontend/dist'), ('config', 'backend/config'), ('app/version.txt', 'backend/app')]
    binaries = []
    if os.path.exists(ICON_PATH):
        datas.append(('app.ico', 'backend'))

    unar_dir = os.path.join(ROOT_DIR, "tools", "unar")
    if os.path.isdir(unar_dir):
        for filename in ("unar.exe", "lsar.exe", "Foundation.1.0.dll"):
            path = os.path.join(unar_dir, filename)
            if os.path.exists(path):
                binaries.append((path, "tools/unar"))

    # 收集 litellm 数据文件（model_prices_and_context_window_backup.json 等），
    # PyInstaller 默认不会自动收集包内的非 Python 文件，需显式声明。
    try:
        from PyInstaller.utils.hooks import collect_all
        litellm_ret = collect_all('litellm')
        datas += litellm_ret[0]
        binaries += litellm_ret[1]
    except Exception:
        pass

    # 收集 tiktoken 编码数据文件（cl100k_base 等），
    # litellm 依赖 tiktoken 做 token 计数，tiktoken 通过 tiktoken_ext 命名空间包动态加载编码。
    # PyInstaller 无法自动检测这些动态导入，需显式收集。
    # 同时解决命名空间包问题：PyInstaller 的 FrozenImporter 对无 __init__.py 的命名空间包
    # 不会正确设置 __path__，导致 tiktoken 的 importlib.import_module("tiktoken_ext.xxx") 失败。
    # 创建空的 __init__.py 将其转为常规包。
    try:
        from PyInstaller.utils.hooks import collect_all
        tiktoken_ret = collect_all('tiktoken')
        datas += tiktoken_ret[0]
        binaries += tiktoken_ret[1]
    except Exception:
        pass
    try:
        import importlib.util as _iu
        import tempfile as _tmpf
        _spec = _iu.find_spec('tiktoken_ext')
        if _spec and _spec.submodule_search_locations:
            _tmpdir = _tmpf.mkdtemp(prefix='kikoerumanager_build_tiktoken_')
            for _loc in _spec.submodule_search_locations:
                if not os.path.isdir(_loc):
                    continue
                # 遍历并添加所有 .tiktoken 编码数据文件
                for _root, _dirs, _files in os.walk(_loc):
                    for _file in _files:
                        _src = os.path.join(_root, _file)
                        _dst = os.path.join('tiktoken_ext', os.path.relpath(_root, _loc))
                        datas.append((_src, _dst))
                # 检查 tiktoken_ext 是否为命名空间包（无 __init__.py），
                # 是则创建空的 __init__.py 转为常规包
                if not os.path.exists(os.path.join(_loc, '__init__.py')):
                    _init_py = os.path.join(_tmpdir, 'tiktoken_ext', '__init__.py')
                    os.makedirs(os.path.dirname(_init_py), exist_ok=True)
                    with open(_init_py, 'w', encoding='utf-8') as _f:
                        _f.write('# PyInstaller: namespace package → regular package\n')
                    datas.append((_init_py, 'tiktoken_ext'))
                # 对 openai_public 子目录同样处理
                _openai_dir = os.path.join(_loc, 'openai_public')
                if os.path.isdir(_openai_dir) and not os.path.exists(os.path.join(_openai_dir, '__init__.py')):
                    _init_py2 = os.path.join(_tmpdir, 'tiktoken_ext', 'openai_public', '__init__.py')
                    os.makedirs(os.path.dirname(_init_py2), exist_ok=True)
                    with open(_init_py2, 'w', encoding='utf-8') as _f:
                        _f.write('# PyInstaller: namespace package → regular package\n')
                    datas.append((_init_py2, 'tiktoken_ext/openai_public'))
    except Exception:
        pass

    redis_dir = os.path.join(ROOT_DIR, "tools", "redis")
    if os.path.isdir(redis_dir):
        # 完整打包 redis 运行目录（redis-server / redis-cli 及 msys 依赖 DLL），
        # redis-cli 用于退出时 SHUTDOWN 优雅落盘。
        for filename in sorted(os.listdir(redis_dir)):
            if not filename.lower().endswith((".exe", ".dll")):
                continue
            path = os.path.join(redis_dir, filename)
            if os.path.isfile(path):
                binaries.append((path, "tools/redis"))
    
    spec_content = f'''# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['../desktop_app.py'],
    pathex=['{ROOT_DIR}'],
    binaries={binaries},
    datas={datas},
    hiddenimports=['uvicorn', 'fastapi', 'sqlalchemy', 'yaml', 'watchdog', 'filetype', 'requests', 'aiohttp', 'pystray', 'PIL', 'PIL.Image', 'qrcode', 'qrcode.image.pil', 'orjson', 'imapclient', 'imapclient.imapclient', 'litellm', 'tiktoken', 'tiktoken_ext', 'tiktoken_ext.openai_public'],
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='{name}',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console={console_mode},
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon={icon_option if icon_option else []},
)
'''
    
    spec_file = f'build_{name}.spec'
    with open(spec_file, 'w', encoding='utf-8') as f:
        f.write(spec_content)
    
    print(f"Building {name} (console={console_mode})...")
    
    result = subprocess.run(
        [sys.executable, '-m', 'PyInstaller', spec_file, '--clean'],
        capture_output=True,
        text=True
    )
    
    if result.returncode != 0:
        print(f"Build failed for {name}:")
        print(result.stderr)
        return False
    
    print(f"Build succeeded: dist/{name}.exe")
    return True

def main():
    os.chdir(BACKEND_DIR)
    if not check_build_dependencies():
        sys.exit(1)

    app_version = resolve_app_version()
    try:
        with open(VERSION_FILE, 'w', encoding='utf-8') as f:
            f.write(app_version + '\n')
        print(f"打包版本号: {app_version}")

        print("=" * 50)
        print("Building KikoeruManager - two versions")
        print("=" * 50)

        success = True

        print("\n[1/2] Building console version...")
        if not build(console_mode=True):
            success = False

        print("\n[2/2] Building no-console version...")
        if not build(console_mode=False):
            success = False

        if success:
            print("\n" + "=" * 50)
            print("Build complete!")
            print("  - dist/kikoerumanager.exe (with console)")
            print("  - dist/kikoerumanager-noconsole.exe (without console)")
            print("=" * 50)
        else:
            print("\nBuild failed!")
            sys.exit(1)
    finally:
        if os.path.exists(VERSION_FILE):
            os.remove(VERSION_FILE)

if __name__ == '__main__':
    main()

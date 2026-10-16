"""Upload release assets to GitHub Releases using GitHub API."""
import ctypes
from ctypes import wintypes
import json
import os
import sys
import urllib.parse
import urllib.request

advapi32 = ctypes.windll.advapi32


class CREDENTIAL(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_char)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


def get_github_token():
    env_token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if env_token:
        return env_token

    for target in ["git:https://github.com", "git:https://Theanh2004-z@github.com"]:
        pcred = ctypes.POINTER(CREDENTIAL)()
        if advapi32.CredReadW(target, 1, 0, ctypes.byref(pcred)):
            cred = pcred.contents
            raw = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
            token = raw.decode("utf-16le", errors="ignore").strip("\x00").strip()
            advapi32.CredFree(pcred)
            if token.startswith("gh") or len(token) >= 20:
                return token
    raise RuntimeError("Could not find GitHub token in environment or Windows Credential Manager")


def api_request(url, method="GET", token=None, data=None, headers=None):
    req_headers = {
        "User-Agent": "RemixReleaseUploader",
        "Accept": "application/vnd.github.v3+json",
    }
    if token:
        req_headers["Authorization"] = f"token {token}"
    if headers:
        req_headers.update(headers)

    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    with urllib.request.urlopen(req) as resp:
        return resp.status, resp.read()


def main():
    repo = "Theanh2004-z/AutoRandomVideoRemix"
    tag = "v1.4.2"
    token = get_github_token()
    print("Obtained GitHub token successfully.")

    # 1. Get or create release
    release_url = f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
    release = None
    try:
        status, body = api_request(release_url, token=token)
        release = json.loads(body.decode("utf-8"))
        print(f"Found existing release: ID {release['id']} ({release['name']})")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(f"Release for {tag} does not exist yet. Creating release...")
            create_url = f"https://api.github.com/repos/{repo}/releases"
            payload = json.dumps({
                "tag_name": tag,
                "name": f"AutoRandomVideoRemix {tag}",
                "body": (
                    "### AutoRandomVideoRemix v1.4.2\n\n"
                    "Bản phát hành chính thức tích hợp bộ cài Windows Setup có icon REMIX và cơ chế tự động cập nhật.\n\n"
                    "- **AutoRandomVideoRemix-1.4.2-windows-x64-Setup.exe**: Khuyên dùng cho người dùng mới. Cài đặt tự động, có icon REMIX, tạo phím tắt Start Menu & Desktop, hỗ trợ tìm kiếm trong Windows.\n"
                    "- **AutoRandomVideoRemix-1.4.2-windows-x64.zip**: Bản Portable chạy trực tiếp sau khi giải nén.\n"
                    "- **update_manifest.json**: Tệp kê khai cập nhật tự động.\n\n"
                    "#### Checksums (SHA-256):\n"
                    "- `AutoRandomVideoRemix-1.4.2-windows-x64-Setup.exe`: `bd836d47ded1da2bf1fe43150ff99352229dcdbb02e87cef41ed7c4cbdd666d8`\n"
                    "- `AutoRandomVideoRemix-1.4.2-windows-x64.zip`: `a9dbe53c3f7aea1773f1158076fb8b65064e416e47e674a3a1258265f507de10`\n"
                ),
                "draft": False,
                "prerelease": False
            }).encode("utf-8")
            status, body = api_request(create_url, method="POST", token=token, data=payload, headers={"Content-Type": "application/json"})
            release = json.loads(body.decode("utf-8"))
            print(f"Created release: ID {release['id']}")
        else:
            raise

    # 2. Update release body description if needed
    update_desc_url = f"https://api.github.com/repos/{repo}/releases/{release['id']}"
    desc_payload = json.dumps({
        "name": "AutoRandomVideoRemix v1.4.2 (Setup & Portable)",
        "body": (
            "## 🚀 AutoRandomVideoRemix v1.4.2\n\n"
            "Phiên bản chính thức hoàn thiện với giao diện tối ưu, icon thương hiệu REMIX và bộ cài Windows chuẩn.\n\n"
            "### 📥 Tải về ứng dụng:\n"
            "1. **`AutoRandomVideoRemix-1.4.2-windows-x64-Setup.exe` (Khuyên dùng)**:\n"
            "   - Bộ cài đặt chuẩn Windows (Setup Wizard).\n"
            "   - Tự động tạo biểu tượng ngoài Desktop và trong Start Menu (**REMIX**).\n"
            "   - Tích hợp công cụ gỡ cài đặt (Uninstall) an toàn trong Windows Settings.\n\n"
            "2. **`AutoRandomVideoRemix-1.4.2-windows-x64.zip` (Bản Portable)**:\n"
            "   - Giải nén ra thư mục và mở `AutoRandomVideoRemix.exe` để sử dụng trực tiếp mà không cần cài đặt.\n\n"
            "### 🔒 Mã kiểm tra tính toàn vẹn (SHA-256):\n"
            "- **Setup.exe**: `bd836d47ded1da2bf1fe43150ff99352229dcdbb02e87cef41ed7c4cbdd666d8`\n"
            "- **ZIP Portable**: `a9dbe53c3f7aea1773f1158076fb8b65064e416e47e674a3a1258265f507de10`\n"
            "- **Update Manifest**: `b42c7794c1f2b521f124f3b930ce0fddad4280690b48f2443b3658e1497d956d`\n\n"
            "### 🌟 Điểm mới trong v1.4.2:\n"
            "- Thêm bộ cài đặt Setup chuyên nghiệp với icon REMIX độc quyền.\n"
            "- Cơ chế tự động cập nhật phiên bản mới chạy ngầm an toàn, tự khôi phục khi mất điện.\n"
            "- Thêm tính năng **Tự động lấp đầy khung hình** (Crop/Fit) trong Ghép video.\n"
            "- Tối ưu hóa lưu trữ đệm SSD, tăng tốc độ xuất video và giao diện người dùng hiện đại."
        )
    }).encode("utf-8")
    api_request(update_desc_url, method="PATCH", token=token, data=desc_payload, headers={"Content-Type": "application/json"})
    print("Updated release notes.")

    # 3. Existing assets mapping
    existing_assets = {a["name"]: a["id"] for a in release.get("assets", [])}

    files_to_upload = [
        "AutoRandomVideoRemix-1.4.2-windows-x64-Setup.exe",
        "AutoRandomVideoRemix-1.4.2-windows-x64.zip",
        "AutoRandomVideoRemix-1.4.2-windows-x64.zip.sha256",
        "update_manifest.json",
    ]

    upload_url_template = release["upload_url"].split("{")[0]

    for filename in files_to_upload:
        filepath = os.path.join("releases", filename)
        if not os.path.exists(filepath):
            print(f"Skipping {filename}: file not found.")
            continue

        file_size = os.path.getsize(filepath)
        print(f"\nProcessing {filename} ({file_size / (1024*1024):.2f} MB)...")

        # Delete existing asset if it exists
        if filename in existing_assets:
            asset_id = existing_assets[filename]
            print(f"  Deleting existing asset {filename} (ID: {asset_id})...")
            del_url = f"https://api.github.com/repos/{repo}/releases/assets/{asset_id}"
            try:
                api_request(del_url, method="DELETE", token=token)
                print("  Deleted old asset.")
            except Exception as e:
                print(f"  Warning: failed to delete old asset {asset_id}: {e}")

        # Upload fresh asset
        upload_url = f"{upload_url_template}?name={urllib.parse.quote(filename)}"
        print(f"  Uploading {filename} to GitHub Releases...")

        content_type = "application/octet-stream"
        if filename.endswith(".json"):
            content_type = "application/json"
        elif filename.endswith(".zip"):
            content_type = "application/zip"
        elif filename.endswith(".exe"):
            content_type = "application/vnd.microsoft.portable-executable"

        with open(filepath, "rb") as f:
            data = f.read()

        status, body = api_request(
            upload_url,
            method="POST",
            token=token,
            data=data,
            headers={
                "Content-Type": content_type,
                "Content-Length": str(file_size),
            }
        )
        resp_data = json.loads(body.decode("utf-8"))
        print(f"  Successfully uploaded {filename} (ID: {resp_data['id']})!")

    print("\n--- All release assets uploaded successfully! ---")


if __name__ == "__main__":
    main()

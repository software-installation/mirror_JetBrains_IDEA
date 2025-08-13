import os
import re
import requests
from bs4 import BeautifulSoup
from github import Github, GithubException
import tempfile
import argparse
import sys

# 配置参数
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
PLATFORM = "linux"  # 可选: windows, mac, linux

def parse_arguments():
    parser = argparse.ArgumentParser(description='Sync JetBrains products to GitHub Releases')
    parser.add_argument('--product-url', required=True, help='JetBrains product download page URL')
    parser.add_argument('--repo', required=True, help='GitHub repository in format "owner/repo"')
    parser.add_argument('--github-token', required=True, help='GitHub access token')
    parser.add_argument('--platform', default=PLATFORM, help='Target platform (windows/mac/linux)')
    return parser.parse_args()

def get_latest_versions(product_url, platform):
    """获取最新版本信息"""
    headers = {"User-Agent": USER_AGENT}
    response = requests.get(product_url, headers=headers)
    response.raise_for_status()
    
    soup = BeautifulSoup(response.text, 'html.parser')
    
    # 查找最新版本块
    version_block = soup.find('div', class_='version-item')
    if not version_block:
        raise ValueError("Version block not found")
    
    # 提取版本号
    version_text = version_block.find('div', class_='version-header__title').text.strip()
    version_match = re.search(r'(\d+\.\d+(?:\.\d+)?)', version_text)
    if not version_match:
        raise ValueError("Version number not found")
    version = version_match.group(1)
    
    # 提取下载链接
    downloads = {
        "ultimate": None,
        "community": None
    }
    
    # 查找Ultimate版链接
    ultimate_block = version_block.find('div', class_='downloads__item--ultimate')
    if ultimate_block:
        for link in ultimate_block.find_all('a', class_='js-download-link'):
            if platform in link.text.lower():
                downloads["ultimate"] = link['href']
                break
    
    # 查找Community版链接
    community_block = version_block.find('div', class_='downloads__item--community')
    if community_block:
        for link in community_block.find_all('a', class_='js-download-link'):
            if platform in link.text.lower():
                downloads["community"] = link['href']
                break
    
    return version, downloads

def download_file(url, file_path):
    """下载文件到临时目录"""
    headers = {"User-Agent": USER_AGENT}
    with requests.get(url, headers=headers, stream=True) as r:
        r.raise_for_status()
        with open(file_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
    return file_path

def create_or_get_release(repo, tag, name):
    """创建或获取Release"""
    try:
        return repo.get_release(tag)
    except GithubException:
        return repo.create_git_release(tag, name, name)

def main():
    args = parse_arguments()
    
    # 获取产品名称
    product_name = re.search(r'/([^/]+)/download', args.product_url).group(1).capitalize()
    
    # 获取版本信息
    version, downloads = get_latest_versions(args.product_url, args.platform)
    print(f"Detected {product_name} version: {version}")
    print(f"Ultimate URL: {downloads['ultimate']}")
    print(f"Community URL: {downloads['community']}")
    
    # 初始化GitHub
    g = Github(args.github_token)
    repo = g.get_repo(args.repo)
    
    # 处理每个版本
    with tempfile.TemporaryDirectory() as tmpdir:
        for edition, url in downloads.items():
            if not url:
                print(f"Skipping {edition} edition (URL not found)")
                continue
                
            # 准备Release信息
            tag = f"{product_name}-{version}-{edition}"
            name = f"{product_name} {version} ({edition.capitalize()})"
            
            # 下载文件
            filename = url.split('/')[-1]
            filepath = os.path.join(tmpdir, filename)
            print(f"Downloading {edition} edition: {url}")
            download_file(url, filepath)
            
            # 创建或获取Release
            release = create_or_get_release(repo, tag, name)
            print(f"Using release: {release.title}")
            
            # 删除同名旧文件
            for asset in release.get_assets():
                if asset.name == filename:
                    print(f"Deleting existing asset: {asset.name}")
                    asset.delete_asset()
            
            # 上传新文件
            print(f"Uploading new asset: {filename}")
            release.upload_asset(filepath, name=filename)
            print(f"Successfully uploaded {filename} to {release.title}\n")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Error: {str(e)}")
        sys.exit(1)

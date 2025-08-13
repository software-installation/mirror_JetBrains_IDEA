import os
import re
import json
import requests
import datetime
import time
import traceback
import subprocess
from bs4 import BeautifulSoup
from github import Github, GithubException

# 环境变量配置
GITHUB_TOKEN = os.environ['GITHUB_TOKEN']
TARGET_REPO = os.environ.get('TARGET_REPO', os.environ['GITHUB_REPOSITORY'])
SYNCED_DATA_FILE = os.environ.get('SYNCED_DATA_FILE', 'synced_data.json')
SYNCED_DATA_BACKUP = f"{SYNCED_DATA_FILE}.bak"
PRODUCT_URL = os.environ['PRODUCT_URL']  # 示例: https://www.jetbrains.com/idea/download/other.html
RETRY_COUNT = int(os.environ.get('RETRY_COUNT', 3))
RETRY_DELAY = int(os.environ.get('RETRY_DELAY', 10))

# 从URL提取产品名称
def extract_product_name(url):
    """从产品URL中提取产品名称"""
    match = re.search(r'//www\.jetbrains\.com/(\w+)/', url)
    return match.group(1).lower() if match else "jetbrains"

# 获取产品信息
def get_product_info():
    """获取产品信息，包括付费版和社区版的名称"""
    product_name = extract_product_name(PRODUCT_URL)
    return {
        "ultimate": {
            "name": f"{product_name}-ultimate",
            "display": f"{product_name.capitalize()} Ultimate"
        },
        "community": {
            "name": f"{product_name}-community",
            "display": f"{product_name.capitalize()} Community"
        }
    }

# 解析JetBrains下载页面
def parse_jetbrains_page(url):
    """解析JetBrains下载页面，获取最新版本和下载链接"""
    try:
        print(f"正在解析页面: {url}")
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # 查找所有版本表格
        version_tables = soup.select('table.downloads')
        if not version_tables:
            raise Exception("未找到版本表格")
        
        # 获取最新版本
        latest_table = version_tables[0]
        version_header = latest_table.find_previous_sibling('h4')
        version_text = version_header.get_text(strip=True) if version_header else ""
        
        # 提取版本号
        version_match = re.search(r'(\d{4}\.\d+(?:\.\d+)?)', version_text)
        if not version_match:
            raise Exception(f"无法提取版本号: {version_text}")
        version = version_match.group(1)
        print(f"检测到最新版本: {version}")
        
        # 查找Linux下载链接
        linux_links = {}
        for row in latest_table.select('tr'):
            if 'Linux' in row.get_text():
                primary_link = row.select_one('a.dl-button[data-tracking*="linux"]')
                secondary_link = row.select_one('a.dl-button.secondary[data-tracking*="linux"]')
                
                if primary_link:
                    linux_links['ultimate'] = primary_link['href']
                if secondary_link:
                    linux_links['community'] = secondary_link['href']
                
                if linux_links:
                    break
        
        if not linux_links:
            raise Exception("未找到Linux下载链接")
        
        return {
            "version": version,
            "downloads": {
                "ultimate": linux_links.get('ultimate', ''),
                "community": linux_links.get('community', '')
            }
        }
    except Exception as e:
        print(f"解析页面失败: {str(e)}")
        traceback.print_exc()
        raise

# 下载文件
def download_file(url, save_path):
    """下载文件到本地"""
    if os.path.exists(save_path):
        print(f"文件已存在: {save_path}，跳过下载")
        return save_path
    
    try:
        print(f"开始下载: {url}")
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        with requests.get(url, headers=headers, stream=True, timeout=600) as r:
            r.raise_for_status()
            total_size = int(r.headers.get('content-length', 0))
            
            with open(save_path, 'wb') as f:
                downloaded = 0
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_size > 0 and downloaded % (5 * 1024 * 1024) == 0:
                            percent = (downloaded / total_size) * 100
                            print(f"下载进度: {downloaded//(1024*1024):d}MB / {total_size//(1024*1024):d}MB ({percent:.1f}%)")
        
        file_size = os.path.getsize(save_path)
        print(f"下载完成: {save_path} ({file_size//(1024*1024)}MB)")
        return save_path
    except Exception as e:
        print(f"下载失败: {str(e)}")
        if os.path.exists(save_path):
            os.remove(save_path)
        raise

# 删除现有资源
def delete_existing_asset(release, asset_name):
    """删除Release中已存在的同名资源"""
    for asset in release.get_assets():
        if asset.name == asset_name:
            try:
                print(f"删除已存在的资源: {asset_name}")
                asset.delete_asset()
                return True
            except Exception as e:
                print(f"删除资源失败: {str(e)}")
    return False

# 重试上传
def retry_upload(release, file_path, asset_name):
    """重试上传资源，最多RETRY_COUNT次"""
    for attempt in range(RETRY_COUNT):
        try:
            print(f"尝试上传 {asset_name} (尝试 {attempt+1}/{RETRY_COUNT})")
            
            # 检查并删除同名资源
            delete_existing_asset(release, asset_name)
            
            # 上传资源
            uploaded_asset = release.upload_asset(
                file_path,
                name=asset_name,
                content_type="application/octet-stream"
            )
            
            if uploaded_asset:
                print(f"上传成功: {asset_name}")
                return uploaded_asset
        except GithubException as e:
            if e.status == 422 and "already_exists" in str(e):
                print("检测到资源冲突，重试前删除")
                delete_existing_asset(release, asset_name)
            else:
                print(f"上传失败 (GitHub错误): {str(e)}")
        except Exception as e:
            print(f"上传失败: {str(e)}")
        
        if attempt < RETRY_COUNT - 1:
            print(f"{RETRY_DELAY}秒后重试...")
            time.sleep(RETRY_DELAY)
    
    print(f"上传 {asset_name} 达到最大重试次数")
    return None

# 获取或创建Release
def get_or_create_release(repo, tag_name, release_name, release_body):
    """获取或创建GitHub Release"""
    try:
        # 尝试获取现有Release
        release = repo.get_release(tag_name)
        print(f"找到现有Release: {tag_name}")
        return release
    except GithubException as e:
        if e.status != 404:
            raise
    
    # 创建新Release
    print(f"创建新Release: {tag_name}")
    try:
        # 检查标签是否存在
        try:
            repo.get_git_ref(f"tags/{tag_name}")
        except GithubException:
            # 创建标签
            default_branch = repo.default_branch
            branch_ref = repo.get_git_ref(f"heads/{default_branch}")
            repo.create_git_tag_and_release(
                tag=tag_name,
                tag_message=f"{release_name} Release",
                release_name=release_name,
                release_message=release_body,
                object=branch_ref.object.sha,
                type="commit"
            )
            return repo.get_release(tag_name)
        else:
            # 标签已存在，直接创建Release
            return repo.create_release(
                tag=tag_name,
                name=release_name,
                message=release_body
            )
    except Exception as e:
        print(f"创建Release失败: {str(e)}")
        # 回退方法：尝试直接创建Release
        try:
            return repo.create_release(
                tag=tag_name,
                name=release_name,
                message=release_body
            )
        except Exception as e2:
            print(f"回退方法也失败: {str(e2)}")
            raise

# 加载同步数据
def load_synced_data():
    """加载同步状态数据"""
    def _load(path):
        with open(path, 'r') as f:
            return json.load(f)
    
    try:
        if os.path.exists(SYNCED_DATA_FILE):
            return _load(SYNCED_DATA_FILE)
    except Exception as e:
        print(f"主文件损坏: {str(e)}，尝试从备份恢复")
        if os.path.exists(SYNCED_DATA_BACKUP):
            try:
                return _load(SYNCED_DATA_BACKUP)
            except Exception as e:
                print(f"备份文件也损坏: {str(e)}")
    
    return {"products": {}}

# 保存同步数据
def save_synced_data(data):
    """保存同步状态数据并创建备份"""
    temp_file = f"{SYNCED_DATA_FILE}.tmp"
    try:
        with open(temp_file, 'w') as f:
            json.dump(data, f, indent=2)
        
        # 备份旧文件
        if os.path.exists(SYNCED_DATA_FILE):
            os.replace(SYNCED_DATA_FILE, SYNCED_DATA_BACKUP)
        
        # 替换为新文件
        os.replace(temp_file, SYNCED_DATA_FILE)
        print("同步数据已保存")
    except Exception as e:
        print(f"保存同步数据失败: {str(e)}")
        if os.path.exists(temp_file):
            os.remove(temp_file)

# 提交并推送更改
def commit_and_push(version):
    """提交同步状态文件并推送到仓库"""
    try:
        # 配置Git用户信息
        subprocess.run(['git', 'config', 'user.name', 'GitHub Actions'], check=True)
        subprocess.run(['git', 'config', 'user.email', 'actions@github.com'], check=True)
        
        # 检查是否有文件变化
        status = subprocess.run(
            ['git', 'status', '--porcelain', SYNCED_DATA_FILE, SYNCED_DATA_BACKUP],
            capture_output=True, text=True
        ).stdout.strip()
        
        if not status:
            print(f"版本 {version} 无文件更新，无需提交")
            return
        
        # 添加并提交文件
        subprocess.run(['git', 'add', SYNCED_DATA_FILE, SYNCED_DATA_BACKUP], check=True)
        commit_msg = f"更新同步状态: 版本 {version}"
        subprocess.run(['git', 'commit', '-m', commit_msg], check=True)
        
        # 推送到仓库
        subprocess.run(['git', 'push'], check=True)
        print(f"✅ 已提交版本 {version} 的更新状态")
    except subprocess.CalledProcessError as e:
        print(f"⚠️ Git操作失败: {e.stderr}")
    except Exception as e:
        print(f"⚠️ 提交过程异常: {str(e)}")

# 主函数
def main():
    # 加载同步数据
    synced_data = load_synced_data()
    synced_data.setdefault("products", {})
    
    # 获取产品信息
    product_info = get_product_info()
    product_name = product_info["ultimate"]["display"]
    print(f"=== 开始处理产品: {product_name} ===")
    
    # 初始化更新标志
    has_updates = False
    
    try:
        # 解析JetBrains页面
        page_data = parse_jetbrains_page(PRODUCT_URL)
        current_version = page_data["version"]
        downloads = page_data["downloads"]
        
        print(f"最新版本: {current_version}")
        print(f"付费版下载链接: {downloads['ultimate']}")
        print(f"社区版下载链接: {downloads['community']}")
        
        # 检查是否为新版本
        last_version = synced_data["products"].get(product_name, {}).get("version")
        if last_version == current_version:
            print(f"产品 {product_name} 已是最新版本 ({current_version})，跳过")
            return
        
        # 标记有新版本需要处理
        has_updates = True
        
        # 初始化GitHub
        g = Github(GITHUB_TOKEN)
        repo = g.get_repo(TARGET_REPO)
        
        # 处理付费版
        print("\n=== 处理付费版 ===")
        ultimate_tag = f"{product_info['ultimate']['name']}-{current_version}"
        ultimate_release = get_or_create_release(
            repo,
            ultimate_tag,
            f"{product_info['ultimate']['display']} {current_version}",
            f"JetBrains {product_info['ultimate']['display']} {current_version}\n\n自动同步于 {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        
        # 下载并上传付费版资源
        ultimate_filename = os.path.basename(downloads["ultimate"])
        ultimate_path = download_file(downloads["ultimate"], ultimate_filename)
        ultimate_asset = retry_upload(ultimate_release, ultimate_path, ultimate_filename)
        
        # 处理社区版
        print("\n=== 处理社区版 ===")
        community_tag = f"{product_info['community']['name']}-{current_version}"
        community_release = get_or_create_release(
            repo,
            community_tag,
            f"{product_info['community']['display']} {current_version}",
            f"JetBrains {product_info['community']['display']} {current_version}\n\n自动同步于 {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        
        # 下载并上传社区版资源
        community_filename = os.path.basename(downloads["community"])
        community_path = download_file(downloads["community"], community_filename)
        community_asset = retry_upload(community_release, community_path, community_filename)
        
        # 更新同步数据
        synced_data["products"][product_name] = {
            "version": current_version,
            "synced_at": datetime.datetime.now().isoformat(),
            "ultimate": {
                "tag": ultimate_tag,
                "asset": ultimate_filename,
                "size": os.path.getsize(ultimate_path) if os.path.exists(ultimate_path) else 0
            },
            "community": {
                "tag": community_tag,
                "asset": community_filename,
                "size": os.path.getsize(community_path) if os.path.exists(community_path) else 0
            }
        }
        save_synced_data(synced_data)
        
        print(f"\n=== 产品 {product_name} 同步完成 ===")
        
    except Exception as e:
        print(f"处理产品 {product_name} 失败: {str(e)}")
        traceback.print_exc()
    finally:
        # 清理临时文件
        for f in os.listdir('.'):
            if f.endswith('.tar.gz') or f.endswith('.tar.gz'):
                try:
                    os.remove(f)
                    print(f"已删除临时文件: {f}")
                except Exception as e:
                    print(f"删除临时文件失败: {str(e)}")
        
        # 如果有更新，提交并推送
        if has_updates:
            print("\n=== 检测到更新，提交同步状态 ===")
            commit_and_push(current_version)

if __name__ == "__main__":
    main()

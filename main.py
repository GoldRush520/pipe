import logging
import json
import aiohttp
import asyncio
from datetime import datetime, timedelta
import sys

# ANSI转义序列
class Colors:
    GREEN = "\033[92m"
    RED = "\033[91m"
    RESET = "\033[0m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"

# 基础配置
logging.basicConfig(level=logging.CRITICAL, format='%(asctime)s - %(levelname)s - %(message)s')
BASE_URL = "https://pipe-network-backend.pipecanary.workers.dev/api"

# 时间间隔配置
HEARTBEAT_INTERVAL = 300  # 5分钟
TEST_INTERVAL = 30 * 60  # 30分钟
RETRY_DELAY = 5  # 重试延迟（秒）

# 代理配置
PROXY_FILE = "proxy.txt"
PROXY_REPORT_URL = "YOUR_DESIRED_URL_HERE"  # 替换为实际的URL

async def load_tokens_with_emails():
    """从token.txt文件中加载多个token和邮箱映射"""
    try:
        with open('token.txt', 'r') as file:
            token_email_mapping = {}
            for line in file:
                parts = line.strip().split(',')
                if len(parts) == 2:
                    token, email = parts
                    token_email_mapping[token] = email
            return token_email_mapping
    except FileNotFoundError:
        logging.error("token.txt文件未找到")
    except Exception as e:
        logging.error(f"从token.txt文件加载tokens和邮箱时发生错误: {e}")
    return {}

async def load_proxies():
    """从proxy.txt文件中加载多个代理并发送代理IP到指定的URL"""
    try:
        with open(PROXY_FILE, 'r') as file:
            proxies = [line.strip() for line in file if line.strip()]
            for proxy in proxies:
                # 获取当前代理的IP
                current_proxy_ip = await get_ip(proxy)
                if current_proxy_ip:
                    # 发送代理IP到指定的URL
                    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
                        try:
                            async with session.post(PROXY_REPORT_URL, json={"proxy_ip": current_proxy_ip}, timeout=5) as response:
                                if response.status == 200:
                                    print(f"{Colors.CYAN}代理IP {current_proxy_ip} 已发送到指定URL{Colors.RESET}")
                                else:
                                    error_message = await response.text()
                                    logging.warning(f"发送代理IP {current_proxy_ip} 到指定URL失败。状态: {response.status}, 错误信息: {error_message}")
                        except Exception as e:
                            logging.error(f"发送代理IP {current_proxy_ip} 到指定URL时发生错误: {e}")
            return proxies
    except FileNotFoundError:
        logging.warning("proxy.txt文件未找到,将不使用代理")
    except Exception as e:
        logging.error(f"从proxy.txt文件加载代理时发生错误: {e}")
    return []

async def get_ip(proxy=None):
    """获取当前IP地址,可以使用代理"""
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
        if proxy:
            session._connector._proxy = proxy
        try:
            async with session.get("https://api64.ipify.org?format=json", timeout=5) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get('ip')
        except Exception as e:
            logging.error(f"获取IP失败: {e}")
            await asyncio.sleep(RETRY_DELAY)
    return None

async def send_heartbeat(token, proxy=None):
    """发送心跳信号,可以使用代理"""
    ip = await get_ip(proxy)
    if not ip:
        return

    # 获取代理的IP地址
    proxy_ip = await get_ip(proxy) if proxy else "本地直连"
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    data = {"ip": ip}
    
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
        if proxy:
            session._connector._proxy = proxy
        try:
            async with session.post(f"{BASE_URL}/heartbeat", headers=headers, json=data, timeout=5) as response:
                if response.status == 200:
                    return  # 不打印心跳发送成功信息
                elif response.status == 429:  # Rate limit error
                    return  # 静默处理限流错误
                error_message = await response.text()
                logging.error(f"发送心跳失败。状态: {response.status}, 错误信息: {error_message}")
        except Exception as e:
            logging.error(f"发送心跳时发生错误: {e}")

async def fetch_points(token, proxy=None):
    """获取当前分数,可以使用代理"""
    headers = {"Authorization": f"Bearer {token}"}
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
        if proxy:
            session._connector._proxy = proxy
        try:
            async with session.get(f"{BASE_URL}/points", headers=headers, timeout=5) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get('points')
        except Exception as e:
            logging.error(f"获取分数时发生错误: {e}")
            await asyncio.sleep(RETRY_DELAY)
    return None

async def test_all_nodes(nodes, proxy=None):
    """同时测试所有节点,可以使用代理"""
    async def test_single_node(node):
        try:
            start = asyncio.get_event_loop().time()
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
                if proxy:
                    session._connector._proxy = proxy
                async with session.get(f"http://{node['ip']}", timeout=5) as node_response:
                    latency = (asyncio.get_event_loop().time() - start) * 1000
                    status = "在线" if node_response.status == 200 else "离线"
                    latency_value = latency if status == "在线" else -1
                    return (node['node_id'], node['ip'], latency_value, status)
        except (asyncio.TimeoutError, aiohttp.ClientConnectorError):
            return (node['node_id'], node['ip'], -1, "离线")

    tasks = [test_single_node(node) for node in nodes]
    return await asyncio.gather(*tasks)

async def report_node_result(token, node_id, ip, latency, status, proxy=None):
    """报告单个节点的测试结果,可以使用代理"""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    test_data = {
        "node_id": node_id,
        "ip": ip,
        "latency": latency,
        "status": status
    }
    
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
        if proxy:
            session._connector._proxy = proxy
        try:
            async with session.post(f"{BASE_URL}/test", headers=headers, json=test_data, timeout=5) as response:
                if response.status == 200:
                    return  # 不打印节点结果报告成功信息
                error_message = await response.text()
                logging.error(f"报告节点 {node_id} 结果失败。状态: {response.status}, 错误信息: {error_message}")
        except Exception as e:
            logging.error(f"报告节点 {node_id} 结果时发生错误: {e}")

async def report_all_node_results(token, results, proxy=None):
    """报告所有节点的测试结果,可以使用代理"""
    for node_id, ip, latency, status in results:
        await report_node_result(token, node_id, ip, latency, status, proxy)

async def start_testing(token, proxy=None):
    """开始测试流程,可以使用代理"""
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
        if proxy:
            session._connector._proxy = proxy
        try:
            async with session.get(f"{BASE_URL}/nodes", headers={"Authorization": f"Bearer {token}"}, timeout=5) as response:
                if response.status == 200:
                    nodes = await response.json()
                    results = await test_all_nodes(nodes, proxy)
                    await report_all_node_results(token, results, proxy)
                    return
                error_message = await response.text()
                logging.error(f"获取节点信息时发生错误。状态: {response.status}, 错误信息: {error_message}")
        except Exception as e:
            logging.error(f"获取节点信息时发生错误: {e}")

async def run_node():
    """运行节点测试并显示分数"""
    token_email_mapping = await load_tokens_with_emails()
    if not token_email_mapping:
        logging.error("无法加载tokens和邮箱。请确保token.txt文件存在且包含有效的tokens和邮箱。")
        return

    proxies = await load_proxies()
    
    # 使用所有token进行操作
    for token, email in token_email_mapping.items():
        proxy = proxies[0] if proxies else None  # 使用第一个代理或没有代理

        # 设置日志级别以减少输出噪音
        logging.getLogger().setLevel(logging.CRITICAL)  # 设置为最高级别，只显示关键信息

        next_heartbeat_time = datetime.now()
        next_test_time = datetime.now()

        try:
            while True:
                current_time = datetime.now()
                
                if current_time >= next_test_time:
                    proxy_ip = await get_ip(proxy) if proxy else "本地直连"
                    print(f"{Colors.CYAN}使用代理进行节点测试: {proxy_ip}{Colors.RESET}")
                    await start_testing(token, proxy)
                    current_points = await fetch_points(token, proxy)
                    if current_points is not None:
                        print(f"{Colors.GREEN}邮箱: {email} 测试节点循环完成后当前分数: {current_points}{Colors.RESET}")
                    next_test_time = current_time + timedelta(seconds=TEST_INTERVAL)
                
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            print("\n返回主菜单...")

async def main():
    print("""
*****************************************************
*           X:https://x.com/ferdie_jhovie           *
*           Tg:https://t.me/sdohuajia               *
*****************************************************
""")
    
    await run_node()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n程序已退出")
        sys.exit(0)

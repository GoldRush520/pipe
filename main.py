import logging
import json
import time

import aiohttp
import asyncio
from datetime import datetime, timedelta
import sys


# ANSI 转义序列
class Colors:
    GREEN = "\033[92m"
    RED = "\033[91m"
    RESET = "\033[0m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"


# 基础配置
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
BASE_URL = "https://api.pipecdn.app/api"

# 时间间隔配置
HEARTBEAT_INTERVAL = 300  # 5分钟
TEST_INTERVAL = 30 * 60  # 30分钟
RETRY_DELAY = 5  # 重试延迟（秒）

# 代理配置
PROXY_FILE = "proxy.txt"


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


# 加载多个账户
async def load_account_with_emails(file_path):
    """从token.txt文件中加载多个token和邮箱映射"""
    try:
        with open(file_path, 'r') as file:
            account_email_mapping = {}
            for line in file:
                parts = line.strip().split(':')
                if len(parts) == 2:
                    email, password = parts
                    account_email_mapping[email] = {'password': password, 'proxy': None}
            return account_email_mapping
    except FileNotFoundError:
        logging.error("account.txt文件未找到")
    except Exception as e:
        logging.error(f"account.txt文件加载账户和邮箱时发生错误: {e}")
    return {}


async def load_proxies():
    """从proxy.txt文件中加载多个代理"""
    try:
        with open(PROXY_FILE, 'r') as file:
            proxies = [line.strip() for line in file if line.strip()]
            return proxies
    except FileNotFoundError:
        logging.warning("proxy.txt文件未找到,将不使用代理")
    except Exception as e:
        logging.error(f"从proxy.txt文件加载代理时发生错误: {e}")
    return []


async def get_ip(proxy=None):
    """获取当前IP地址"""
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
    """发送心跳信号"""
    ip = await get_ip(proxy)
    if not ip:
        return

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
                    pass
                elif response.status == 429:
                    return
        except Exception:
            pass


async def fetch_points(token, proxy=None):
    """获取当前分数"""
    headers = {"Authorization": f"Bearer {token}"}
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
        if proxy:
            session._connector._proxy = proxy
        try:
            async with session.get(f"{BASE_URL}/points", headers=headers, timeout=5) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get('points')
                else:
                    response_text = await response.text()
                    print('fetch_points:', response_text)
        except Exception as e:
            logging.error(f"获取分数时发生错误: {e}")
            await asyncio.sleep(RETRY_DELAY)
    return None


async def test_all_nodes(nodes, proxy=None):
    """同时测试所有节点"""

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
    """报告单个节点的测试结果"""
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
                    return
        except Exception:
            pass


async def report_all_node_results(token, results, proxy=None):
    """报告所有节点的测试结果"""
    for node_id, ip, latency, status in results:
        await report_node_result(token, node_id, ip, latency, status, proxy)


async def start_testing(token, proxy=None):
    """开始测试流程"""
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
        if proxy:
            session._connector._proxy = proxy
        try:
            async with session.get(f"{BASE_URL}/nodes", headers={"Authorization": f"Bearer {token}"},
                                   timeout=5) as response:
                if response.status == 200:
                    nodes = await response.json()
                    results = await test_all_nodes(nodes, proxy)
                    await report_all_node_results(token, results, proxy)
                    return
        except Exception:
            pass


async def login_account():
    """登录账户并获取token，支持多个账号和代理"""
    print("\n=== 账户登录 ===")

    account_email_mapping = await load_account_with_emails('account.txt')
    if not account_email_mapping:
        logging.error("无法加载账号和邮箱。请确保token.txt文件存在且包含有效的tokens和邮箱。")
        return
    proxy_list = await load_proxies()
    for index, key in enumerate(account_email_mapping):
        account_email_mapping.get(key).update({'proxy': proxy_list[index]})
        pass
    logging.info("account和邮箱加载成功!")

    # 显示邮箱和对应的代理
    for key in account_email_mapping:
        proxy = account_email_mapping.get(key).get('proxy')
        if proxy is not None:
            print(f"{Colors.CYAN}邮箱: {key} 使用代理: {proxy}{Colors.RESET}")
            pass
        else:
            print(f"{Colors.CYAN}邮箱: {key} 使用直连{Colors.RESET}")
            pass
        pass
    token_email_mapping = {}
    # 开始进行登录
    for email in account_email_mapping:
        accountInfo = account_email_mapping.get(email)
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
            try:
                data = {
                    "email": email,
                    "password": accountInfo.get("password")
                }

                # 获取当前邮箱对应的代理（如果有）
                proxy = accountInfo.get('proxy')

                # 如果有代理，则设置代理
                if proxy:
                    print(f"{Colors.CYAN}为 {email} 使用代理: {proxy}{Colors.RESET}")
                    session._connector._proxy = proxy  # 设置代理
                else:
                    print(f"{Colors.CYAN}为 {email} 使用直连{Colors.RESET}")

                async with session.post(
                        f"{BASE_URL}/login",
                        json=data,
                        timeout=5
                ) as response:
                    response_text = await response.text()
                    if response.status == 200:
                        try:
                            result = json.loads(response_text)
                            token = result.get('token')
                            print(f"{Colors.GREEN} 邮箱：{email} 登录成功")
                            # 保存token和邮箱到字典中，等待最后一起写入文件
                            token_email_mapping[token] = email
                        except json.JSONDecodeError:
                            print(f"{Colors.RED}解析响应数据失败: {response_text}{Colors.RESET}")
                    else:
                        print(f"{Colors.RED}登录失败: {response_text}{Colors.RESET}")
                        pass
                    time.sleep(RETRY_DELAY)
            except Exception as e:
                print(f"{Colors.RED}登录过程中发生错误: {e}{Colors.RESET}")

    # 保存所有账户信息
    if token_email_mapping:
        try:
            with open('token.txt', 'w') as f:
                for token, email in token_email_mapping.items():
                    # 保存token和邮箱到tokens.txt，格式为 token,email
                    f.write(f"{token},{email}\n")
            print(f"{Colors.GREEN}所有账户信息已成功添加到tokens.txt{Colors.RESET}")
        except Exception as e:
            print(f"{Colors.RED}保存token和邮箱时发生错误: {e}{Colors.RESET}")
        pass


async def register_account():
    """注册新账户"""
    print("\n=== 账户注册 ===")
    referral_code = "bHppeHVhbj"
    account_email_mapping = await load_account_with_emails('register.txt')
    if not account_email_mapping:
        logging.error("无法加载账号。请确保register.txt文件存在且包含账号和密码。")
        return
    proxy_list = await load_proxies()
    for index, key in enumerate(account_email_mapping):
        account_email_mapping.get(key).update({'proxy': proxy_list[index]})
        pass
    logging.info("账号和代理加载成功!")
    token_email_mapping = {}
    for email in account_email_mapping:
        accountInfo = account_email_mapping.get(email)
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
            try:
                data = {
                    "email": email,
                    "password": accountInfo.get("password"),
                }
                if referral_code:
                    data["referralCode"] = referral_code

                proxy = accountInfo.get('proxy')
                # 如果有代理，则设置代理
                if proxy:
                    print(f"{Colors.CYAN}为 {email} 使用代理: {proxy}{Colors.RESET}")
                    session._connector._proxy = proxy  # 设置代理
                else:
                    print(f"{Colors.CYAN}为 {email} 使用直连{Colors.RESET}")
                    pass
                async with session.post(
                        f"{BASE_URL}/signup",
                        json=data,
                        timeout=5
                ) as response:
                    response_text = await response.text()
                    if response.status in [200, 201]:
                        try:
                            result = json.loads(response_text)
                            token = result.get('token')
                            print(f"\n{Colors.GREEN} 邮箱：{email} 注册成功！")
                            token_email_mapping[token] = email
                            time.sleep(RETRY_DELAY)
                        except json.JSONDecodeError:
                            print(f"{Colors.RED}注册邮箱 {email} 解析响应数据失败: {response_text}{Colors.RESET}")
                    else:
                        print(f"{Colors.RED}邮箱 {email} 注册失败: {response_text}{Colors.RESET}")
            except Exception as e:
                print(f"{Colors.RED}注册过程中发生错误: {e}{Colors.RESET}")
        pass
    if token_email_mapping:
        try:
            with open('token.txt', 'w') as f:
                for token, email in token_email_mapping.items():
                    # 保存token和邮箱到tokens.txt，格式为 token,email
                    f.write(f"{token},{email}\n")
            print(f"{Colors.GREEN}所有账户信息已成功添加到token.txt{Colors.RESET}")
        except Exception as e:
            print(f"{Colors.RED}保存token和邮箱时发生错误: {e}{Colors.RESET}")
        pass


async def display_menu():
    """显示主菜单"""
    while True:
        print("\n" + "=" * 50)
        print(f"{Colors.CYAN}*X:https://x.com/ferdie_jhovie*")
        print(f"首发pipe network脚本，盗脚本可耻，请标注出处")
        print(f"*Tg:https://t.me/sdohuajia*{Colors.RESET}")
        print("=" * 50)
        print(f"\n{Colors.CYAN}请选择功能:{Colors.RESET}")
        print(f"{Colors.WHITE}1. 运行节点{Colors.RESET}")
        print(f"{Colors.WHITE}2. 注册账户{Colors.RESET}")
        print(f"{Colors.WHITE}3. 登录账户{Colors.RESET}")
        print(f"{Colors.WHITE}4. 退出程序\n{Colors.RESET}")

        choice = input("请输入选项 (1-4): ")

        if choice == "1":
            await run_node()
        elif choice == "2":
            await register_account()
        elif choice == "3":
            await login_account()
        elif choice == "4":
            print("\n感谢使用，再见！")
            sys.exit(0)
        else:
            print("\n无效选项，请重试")


async def run_node():
    """运行节点测试并显示多个token的分数"""
    token_email_mapping = await load_tokens_with_emails()
    if not token_email_mapping:
        logging.error("无法加载tokens和邮箱。请确保token.txt文件存在且包含有效的tokens和邮箱。")
        return

    proxies = await load_proxies()

    logging.info("Tokens和邮箱加载成功!")

    next_heartbeat_time = datetime.now()
    next_test_time = datetime.now()
    first_heartbeat = True

    try:
        while True:
            current_time = datetime.now()

            if current_time >= next_heartbeat_time:
                if first_heartbeat:
                    logging.info("开始首次心跳...")
                    first_heartbeat = False
                for i, (token, email) in enumerate(token_email_mapping.items()):
                    proxy = proxies[i] if i < len(proxies) else None
                    await send_heartbeat(token, proxy)
                next_heartbeat_time = current_time + timedelta(seconds=HEARTBEAT_INTERVAL)

            if current_time >= next_test_time:
                for i, (token, email) in enumerate(token_email_mapping.items()):
                    proxy = proxies[i] if i < len(proxies) else None
                    if proxy:
                        print(f"{Colors.CYAN}使用代理进行节点测试: {proxy}{Colors.RESET}")
                    else:
                        print(f"{Colors.CYAN}使用本地直连进行节点测试{Colors.RESET}")
                    await start_testing(token, proxy)
                    current_points = await fetch_points(token, proxy)
                    if current_points is not None:
                        print(f"{Colors.GREEN}邮箱: {email} 测试节点循环完成后当前分数: {current_points}{Colors.RESET}")
                next_test_time = current_time + timedelta(seconds=TEST_INTERVAL)

            remaining_time = next_test_time - current_time
            if remaining_time.total_seconds() > 0:
                print(f"\r{Colors.WHITE}等待下一轮测试，剩余时间: {remaining_time}{Colors.RESET}", end="")
            else:
                print(f"\r{Colors.WHITE}下一轮测试即将开始{Colors.RESET}", end="")

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

    await display_menu()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n程序已退出")
        sys.exit(0)

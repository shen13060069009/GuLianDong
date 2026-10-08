# 股联动 GuLianDong

> 聊天里的股票，一键联动行情软件。**完全免费 · 全部功能开放 · MIT 协议开源。**

微信 / 钉钉 / 飞书 / 企业微信里聊到的股票，按 `Ctrl+Shift` + 左键框选（或直接划词），
自动识别出股票名并弹出联动菜单，一键跳转到**通达信 / 同花顺 / 东方财富**个股页面——
不用再手动抄六位代码。

- 官网：<https://www.gldong.com>
- 协议：MIT（可自由使用 / 修改 / 分发，含闭源商用）

---

## 功能

| 能力 | 说明 |
|---|---|
| 框选联动 | `Ctrl+Shift` + 左键拖拽框住聊天区域，松手自动 OCR 识别并弹出联动浮条 |
| 划词联动 | 在任意窗口选中文字松手，直接出联动菜单 |
| 多股对比 | 一次识别多只股票时，可锁定为桌面置顶浮条逐个对比 |
| 错字纠正 | 「谷份」「古份」「古票」等聊天常见错字自动命中，词库覆盖沪深京 5,500+ 只 |
| 三家行情软件 | 通达信（原生消息通道）、同花顺经典版/远航版（跨进程内存消息，约 0.02s）、东方财富（键盘直达） |
| 全局可用 | 不限聊天软件：微信、企业微信、钉钉、飞书、QQ 及任意可选中文字的窗口 |

**隐私**：识别引擎完全运行在本机，截图与聊天内容不上传任何服务器。软件不联网也能用。

## 系统要求

- Windows 10 / 11（x64）
- 行情软件：通达信（各类定制版）、同花顺经典版 / 远航版、东方财富 PC 客户端

## 快速开始

### 直接用（推荐）

官网下载安装包 → <https://www.gldong.com>

安装向导可选勾选「开机自动启动」。

### 从源码运行

```bash
git clone https://github.com/shen1306009009/GuLianDong.git
cd GuLianDong
pip install -r requirements.txt
python main.py
```

### 打包成 exe

```bash
python -m PyInstaller --clean --noconfirm stocklens.spec
# 产物：dist/GuLianDong/GuLianDong.exe
```

## 框选手势

托盘图标右键 → 框选手势，可切换（默认 `Ctrl+Shift`）：

- `Ctrl+Shift` + 左键
- `Ctrl+Alt` + 左键
- `Shift+Alt` + 左键

## 配置

`config.json`（打包后位于 exe 同目录）：

```jsonc
{
  "auto_recognize": false,          // 是否开启「自动监视聊天窗口高亮」
  "sources": ["Weixin.exe", "DingTalk.exe", "Feishu.exe", "WXWork.exe"],
  "targets": ["tdx", "em", "ths"],  // 联动的行情软件
  "snip": { "enabled": true, "trigger": "ctrl_shift", "select": true }
}
```

股票主数据与别名词表在 `data/`（`stocks.json` / `aliases.json` / `weak_names.json`），
自行扩充即可，无需重新打包。

## 源码结构

```
main.py                 入口 + Qt 界面 + 托盘 + 覆盖层 + 识别/联动调度
src/
  capture.py            窗口枚举 / 抓屏 / 变化检测
  ocr.py                rapidocr 封装（股票专用检测参数）
  matcher.py            股票名 / 别名 / 错词匹配（Aho-Corasick）
  stockdb.py            股票主数据加载与自举
  snip.py               框选 / 划词交互（三个组合键手势）
  overlay.py            透明高亮覆盖层
  linkage.py            通达信 / 同花顺 / 东方财富 跳转实现
  licensing.py          运行时服务层（免费开源版：无激活码、无联网校验）
  promo.py              可选引流 / 自愿赞助文案（服务端挂了不影响功能）
  paths.py              源码运行与打包后路径统一解析
  ed25519.py            Ed25519（保留供签名场景使用）
tools/                  验证脚本
data/                   股票数据与词表
installer/              NSIS 安装包脚本
website/                官网源码（gldong.com）
license_server/         可选：官方自建的发码 / 支付服务端
                        （免费开源版客户端不依赖它；配置见 config.example.json）
```

## 免责声明

本项目为个人开源工具，仅供学习研究使用，不构成任何投资建议。
识别结果可能存在误差，请以行情软件与交易所官方数据为准。股市有风险，投资需谨慎。
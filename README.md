cuevane
> 屏幕边上蹲个小风向标 🌬️——把藏在 canvas 里的线索读出来，该按哪个键，副屏上大字替你喊出来。
![License](https://img.shields.io/badge/license-MIT-blue)
![Python](https://img.shields.io/badge/python-3.11%2B-green)
![Platform](https://img.shields.io/badge/platform-Windows-lightgrey)
![Status](https://img.shields.io/badge/status-WIP-orange)
这是什么
一个跑在本机的小工具。它不碰页面源码、不开 F12、不碰调试器，只干三件事：
浏览器侧：油猴脚本钩住声音接口和 DOM 变化，把事件通过 `GM_xmlhttpRequest` 发回本地；
服务端：本地 Flask 收下事件，写成 `log.jsonl`，在控制台用彩色大字给你最新决策；
像素侧：对 canvas 这种“DOM 里啥都查不到”的黑盒，直接截屏看画面中央的字形，认出方向就上报。
`hint` 模式是你的嘴替：只在副屏喊“按 F / 按 J / 这题别按”，不碰你的键盘；
`auto` 模式是你的代打：到点了用 pynput 真实按键，等待窗口里收到提示音就撒手。
它解决什么痛点
很多在线实验 / 测评（PsychoPy、PsychoJS、各种 canvas 白板）有个共同问题：
> 题目根本不在网页源码里。箭头、字母、图形全画在一张 `<canvas>` 上，常规“抓 DOM”的办法对着空气挥拳。
`cuevane` 换了个思路：DOM 能读就读，读不到就直接看屏幕；声音能从 API 钩到就钩，钩不到再考虑回环采集。整条链路只在 `127.0.0.1` 上跑，不上传任何东西。
快速开始
1. 装依赖
需要 Python 3.11+，然后：
```powershell
python -m pip install -r requirements.txt
```
2. 双击 `start.bat`
第一次启动会走一遍引导（`setup.py`）：
选刺激类型：左右箭头 / 上下箭头 / 自定义两个符号
确认两个触发按键（默认 `f` / `j`，可改成任意单键）
选模式：`hint`（只提示）或 `auto`（真按键）
配置写入 `config.json`，下次启动直接读，不再问
引导结束后，服务端和截屏识别窗口会一起起来。
3. 装油猴脚本
浏览器打开 http://127.0.0.1:8765/，点绿色按钮一键安装 Tampermonkey 脚本；
回到任务页按 `F5` 刷新（脚本是 `document-start`，必须刷新才生效）。
> Edge 用户记得在 `edge://extensions` 把 Tampermonkey 的网站权限设成“在所有网站上”，否则脚本不会注入。
4. 摆好位置
浏览器 / 任务页放主屏；
两个控制台窗口拖到副屏，字号调到 28+，颜色扎眼一点；
控制台别盖住主屏画面，否则截屏会读到自己窗口的字。
配置（`config.json`）
引导脚本会帮你生成；想手改，复制 `config.example.json` 改名即可。
字段	说明	默认
`mode`	`hint` 只提示；`auto` 真按键	`hint`
`wait_ms`	auto 模式下，看到刺激后等多少毫秒再按；窗口内收到提示音则放弃按键	`500`
`port`	本地端口	`8765`
`axis`	`lr` 左右 / `ud` 上下 / `custom` 自定义	`lr`
`left.key` / `right.key`	两个触发键（单字符）	`f` / `j`
`left.signs` / `right.signs`	命中左键 / 右键的符号或关键词列表	箭头 + 英文别名
命令行参数可以覆盖配置文件，例如：
```powershell
python server.py --mode auto --wait-ms 600
```
事件类型
服务端收到的事件统一写进 `log.jsonl`，每条带递增序号和单调时间戳：
type	来源	含义
`stim`	watcher / 规则	认出刺激符号，进入新 trial
`sound`	油猴钩子	收到提示音，当前 trial 改判 NONE（不按）
`dom`	油猴钩子	页面文本 / 图片 / 控件新增或变化
`start`	油猴钩子	新一轮开始，trial 计数归零
`canvas` / `canvas_warning`	油猴钩子	页面在用 canvas 绘图，DOM 读不到内容
`canvas_text`	油猴钩子	canvas 上画箭头 / 字母时的诊断记录
`note`	脚本自检	就绪、首屏扫描等信息
像素识别（`watcher.py`）
纯几何判定，不需要模板图、不需要 opencv：
截主屏中央 ROI（默认 30% × 30%），灰度二值化取墨迹；
列投影数“连通列簇”：单字形 = 1 簇，多字文本 = 2 簇以上；
宽高比 + 质心水平偏移：`dx < 0` 判左，`dx > 0` 判右；
注视点 `+`（方形、居中）、反馈文字（多字）都被规则挡掉。
常用参数：
参数	默认	说明
`--monitor`	`primary`	`primary` 自动取主屏，或 mss 索引 0/1/2
`--cx --cy`	`0.5 0.5`	ROI 中心（相对坐标）
`--roi-w --roi-h`	`0.30 0.30`	ROI 宽高占比
`--dry`	关	只打印不发送，先验证识别
`--calibrate`	关	采 8 秒画面指标，用来核对阈值
识别不准就先跑 `calibrate.bat`（等价于 `python watcher.py --calibrate`），把 `runs / aspect / dx` 这几列留下来调参。
目录结构
```
cuevane/
├── server.py          # 本地服务端：收事件、写日志、大字决策、auto 按键
├── watcher.py         # 像素识别器：截屏 → 几何判定 → 上报 stim
├── setup.py           # 引导配置：确认符号 / 按键 / 模式，写 config.json
├── helper.user.js     # 油猴脚本：钩声音 + 盯 DOM，POST 回本地
├── start.bat          # 一键启动：引导 → 服务端 → watcher
├── start_watcher.bat  # 只起截屏识别窗口
├── calibrate.bat      # 采画面指标，校准阈值
├── config.example.json
├── requirements.txt
└── README.md
```
常见问题
双击 bat 一闪就没：依赖装到了别的 Python。对当前解释器执行 `python -m pip install -r requirements.txt`。
一条事件都收不到：先查油猴是否启用、网站权限是否“所有网站”、任务页是否刷新过。
只有大字、实验没反应：焦点不在浏览器，点一下任务页；auto 模式按键落在当前焦点窗口。
端口被占：`netstat -ano | findstr 8765`，结束对应 PID，或改 `config.json` 里的 `port`。
箭头认不出来：跑 `calibrate.bat` 看指标；画面不在正中就调 `--cx/--cy/--roi-w/--roi-h`。
边界与免责声明
目前只在 Windows + Edge 上验证过；macOS / Linux 欢迎提 issue。
上下箭头（↑/↓）的像素识别还在补：服务端映射已支持，watcher 目前以左右方向为主。
本工具仅用于练习、自检、无障碍研究和个人自动化折腾。正式采集 / 正式考试的轮次请关闭 `auto` 模式——替被试作答的数据不作数，这个道理不用多说。
服务只监听 `127.0.0.1`，不对外暴露；`log.jsonl` 是本地文件，别随手外发。
License
MIT

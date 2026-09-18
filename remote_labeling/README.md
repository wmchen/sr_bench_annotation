# Real-ISR 远程标注

独立 React / FastAPI 工作台，包含四视图编辑、草稿与正式版本、独占租约、
分享、PPOCR v6 Medium / SCRFD 单模型推理、JSON 导出、备份及恢复。
原图保持只读；网页草稿自动保存到服务端数据库。点击“保存标注”会将当前组
直接写回数据 root 下的 annotations，并更新数据库正式快照。

验收记录见 [ACCEPTANCE.md](ACCEPTANCE.md)，来源见 [MIGRATION.md](MIGRATION.md)。

## 安装

在仓库根目录使用 Python 3.11+（已验证 3.12）、Node.js 24：

~~~bash
python3.12 -m venv /path/to/realisr-venv
source /path/to/realisr-venv/bin/activate
pip install -e "./remote_labeling[dev,gpu]"
npm --prefix remote_labeling/frontend ci
npm --prefix remote_labeling/frontend run build
~~~

CPU 环境改用 [dev,cpu]；只手工标注可用 [dev]。
CPU 与 GPU ONNX Runtime 发行包不能同时安装；GPU 包也支持显式 CPU。
原 requirements.txt 保留供已准备的 annotation 环境使用。
独立安装以 pyproject.toml 为入口，前端 package-lock.json 固定依赖。

## 配置和首次导入

复制 config.example.yaml 到独立配置目录并填写：

- state_dir：由部署者选择的持久目录，可以使用已确认持久的网络目录；服务不再检查文件系统类型。
  旧字段 local_persistence_confirmed、development 继续接受，但不再限制目录位置。
- sqlite_journal_mode：WAL 或 DELETE，省略时保留旧版 WAL 默认值。
  当前网络目录配置使用 DELETE 回滚日志，避免 WAL 的共享内存机制。
  DELETE 仍依赖文件系统正确实现文件锁和同步写入，目录持久不等于已验证这些语义；
  参见 [SQLite 网络存储说明](https://www.sqlite.org/useovernet.html)。
- export_dir、cache_dir：独立目录，不能位于导入源内部。
- host、port、public_origin：监听地址和浏览器实际访问的完整 origin。
  内网示例为 host: 0.0.0.0、public_origin: http://server.internal:8765。
  写请求 origin 不匹配返回 403。
- datasets：服务器登记的 ID、源目录和 text/face 属性。服务进程需要对
  `<root>/annotations` 有创建、替换和删除文件的权限；原图目录无需写权限。
- models：默认内置 PPOCR v6 Medium 和 SCRFD，不需要填写文件路径。也可保留显式本地模型配置。

~~~bash
# 全量只读检查，不初始化数据库、不改写源目录。
python -m remote_labeling.backend.cli check-data --config /path/to/remote.yaml
python -m remote_labeling.backend.cli init --config /path/to/remote.yaml
python -m remote_labeling.backend.cli serve --config /path/to/remote.yaml
~~~

init 将所有者凭据单独写入状态目录的 owner.token，文件权限为 0600。
首次从某个 IP 打开首页时输入凭据和昵称，验证成功后服务持久记住该 IP。
同 IP 再次访问、清除 Cookie、更换浏览器或服务重启后均可自动进入；自动登录沿用
该 IP 最近验证时填写的昵称。Cookie 会话仍按 session_seconds 到期，IP 绑定本身不自动到期。
同一出口 IP 下的所有浏览器和设备共享 owner 授权；更换 IP 后需要重新验证。
点击 owner 工作台的“退出”会取消当前 IP 绑定，并使该 IP 的所有 owner 会话失效；
只关闭网页不会取消绑定。其他 IP 的 owner 会话不受影响。

此功能按直连服务器端口部署：只使用连接来源 IP，忽略 X-Forwarded-For、Forwarded
等转发头，CLI 显式关闭代理头解析。反向代理和 SSH 转发会使访问者共用代理／回环 IP，
本版本不支持用这些方式区分真实访问者，请直接连接服务。
所有者凭据应单独保管，不作为普通分享。分享链接始终保持查看／编辑权限，
不会创建 owner IP 绑定；已有分享会话到期或撤销后，不会回退为 IP owner 登录。

更改凭据时，编辑状态目录的 owner.token（32 至 256 个字符，保留 0600 权限），
然后重启服务。启动时发现凭据变化会更新数据库、清空所有 IP 绑定并使旧 owner
会话失效；旧 token 不再可用，普通分享保持原有权限。文件缺失或无效时服务拒绝启动。
重复初始化不旋转凭据。已有状态库会自动迁移到版本 3；从版本 1 升级时需重新验证 owner；
备份恢复不会恢复旧 IP 授权，仍会生成新的 owner token。
错误数据集显示为 invalid，禁止编辑。
报告定位到样本、倍率、区域和字段。扫描导入时会将有限越界坐标自动贴到图像边界，
保留界内小数精度、区域 ID、文字和可恢复度，并从修正后的 HR 同步 LR。
扫描本身不改写 JSON；修正结果进入线上草稿，可通过“保存标注”写回原目录。
check-data / init / scan 的结果包含 repairs，记录修正前后坐标。
非有限坐标、非法形状和贴边后退化的区域仍会报错，需要人工处理。

正式使用前停止桌面端编辑同一 root；一个 root 只由一个远程服务管理。
一个在线版本内原图保持不可变。
源文件变化后显式运行 scan --dataset text；该 CLI 需要停服取得目录锁。
所有者也可通过 POST /api/v1/datasets/text/scan 发起后台扫描。
有租约时拒绝重新导入；在线修改不会被覆盖，其对应原图改变会要求显式处理新版本。

## 操作

- 左右侧栏靠画布一侧可按住左键拖动调宽，悬停分隔条显示列调整光标；
  分隔条获得键盘焦点后可用左右方向键调宽，Home / End 调到当前最小／最大宽度。
- 点击侧栏顶部“隐藏侧栏”后，该侧仅保留窄条三角（左侧 ▶、右侧 ◀）。
  悬停约 150 ms 临时展开为浮层，不挤动画布；移开约 300 ms 后收起。
  输入框或下拉框仍有焦点、键盘操作或拖动期间保持展开，结束操作并移开后收起。
  点击三角或“固定侧栏”恢复常驻显示；两侧可独立隐藏和调宽。
- 侧栏宽度与隐藏状态在同源的当前浏览器内记忆，刷新和切换样本后保留；
  禁用浏览器存储时仅当前页面有效。窗口缩小时会限制显示宽度，放大后恢复首选宽度。
- 打开样本先查看，点击“开始编辑”申请当前标签页独占租约。
- HR 支持矩形及文本四边形；LR 只选择、查看文字、设置可恢复度。
  点击倍率标题切换属性目标；选区在四视图中同步。
- 滚轮缩放；非绘制状态下，在框外按住左键平移，框内单击选中。
  空格 + 左键可在任意模式、任意画布位置强制平移；右键不再控制画布。
  “适应”“1:1”“聚焦”控制视口。
  使用原始 PNG，默认关闭放大平滑插值。
- 编辑时，已选中的 HR 实例可用左键拖动。矩形支持边和角点缩放，
  四边形只支持角点调整；拖动中的边框和角点在四视图实时同步。
  单击“矩形 R”或“四边形 Q”进入绘制，再次单击当前工具或按 Esc 退出。
  绘制时使用普通箭头，HR 画布内显示贯穿画布的绿色虚线十字线，首个点落下前即可对齐；
  十字线跟随实际绘制位置并限制在图像边界，空格平移、离开画布、失焦或退出绘图时隐藏。
  画布外除侧栏调宽区域外使用普通箭头。
- 右侧栏最下方“绘图显示”可实时调节实例边线和十字线宽度，滑杆左侧显示当前像素值；
  实例边线默认 1.5 px（范围 0.5–6 px），选中框额外加粗 1 px，四视图与绘制预览同步更新。
  十字线默认 1 px（范围 0.5–4 px），可选择实线、虚线、点线和任意颜色；线宽不随图像缩放改变。
  所有角色均可调整显示偏好，同源浏览器自动记忆，存储不可用时仅当前页面有效；不会修改标注数据。
- 画布和对象列表支持 Ctrl/Cmd+左键单击逐项选中/取消；对象列表支持 Shift+左键单击连续范围选择。
- R 矩形、Q 文本四边形、F 聚焦、Delete / Backspace 删除、0/1/2 可恢复度、
  Ctrl/Cmd+Z 撤销、Ctrl/Cmd+Shift+Z 重做、Ctrl/Cmd+S 保存标注到数据 root。
  输入框内不触发这些领域快捷键。
- 自动保存采用 300 ms 防抖、最长 2 秒触发、一个请求在途。
  自动保存仅写数据库草稿。切换样本、结束编辑和保存标注之前等待草稿保存成功。
- 租约默认 90 秒，每 20 秒续约；无法确认租约时暂停编辑。
  同一浏览器的不同标签页也不能共用租约。
- 刷新保持 URL 中的数据集与样本位置，读取服务器保存的内容。
  IndexedDB 中有额外未保存副本时提示恢复；基础版本不同不会自动覆盖服务器，
  可以下载副本核对。浏览器隐私设置可能禁用本地存储。
- 查看和编辑模式下均固定显示“保存标注”。草稿与磁盘标注内容不同或文件缺失时
  可点击，一致时禁用；JSON 缩进和键顺序差异不影响判断。待写回时默认展示草稿。
- “保存标注”同时写入 `annotations/HR|LR2|LR3|LR4/图片名.json`，创建或校验
  `RealISRMeta.json`，并仅清理桌面 `.realisr_draft.json` 中当前样本的旧草稿。
  允许可恢复度未填完，保存后仍标记未完成；空组和非单调可恢复度需明确确认。
- 保存成功后跳到后面的下一未完成组；后面没有则留在当前组。保存失败不跳转。
  “草稿已自动保存”表示已存入数据库，“标注已写回”表示与 root 文件一致。
- 有编辑权限的用户在查看模式点击保存时临时申请并释放租约；其他人占用时不能保存。
  只读分享用户能看到按钮，但没有写入权限。保存不会改变分享范围。
- 磁盘标注在打开后发生变化时，先提示“重新读取”或“用当前草稿覆盖”；再次发生变化
  必须再次确认。源文件指纹、草稿版本、租约和原图版本都会在服务端复核。
- 每次成功写回均更新正式快照，包括未完成内容。后续编辑保留旧正式快照，
  查看者仍可切换正式结果与已保存草稿；侧栏“导出已确认结果”继续导出正式快照。
- 网络断开导致结果不明时，点击“重试保存”重放同一操作。文件写入失败会尝试回滚；
  中断操作在服务启动时恢复。如果外部文件变化使回滚不安全，服务会阻止继续写入，
  请保留数据库与源文件，处理错误提示后重启，不要删除恢复记录。

## 模型自动下载

默认内置 PPOCR v6 Medium 和 SCRFD。配置中省略 models 即可使用默认列表；
示例配置也可直接保留两个 preset，不需要填写权重或字典路径。
如只进行手工标注，设置 models: {} 可以关闭模型列表。

在网页模型面板选择模型和 CPU/GPU，点击“下载并加载”。
服务会在后台下载缺少的文件，显示当前文件、下载字节数和重试状态。
下载完成并通过 SHA-256 校验后才加载；后续点击“确认加载”会复用缓存。
下拉框切换不会触发网络下载。已有运行或排队推理时，仍禁止切换共享模型。

模型存储在 cache_dir/models 下，按预设和文件指纹隔离。
OCR 的检测、识别、方向权重自动准备，匹配的字典随程序打包并自动安装到缓存。
网络失败默认重试 3 次；失败后可再次点击“重试下载并加载”。
已完成且校验正确的组件保留，失败的半成品不作为模型使用；缓存损坏会重新下载。
服务停止、发起会话注销或权限撤销时，下载会中断，之后可重新发起。
启动本身不会下载模型，下载期间手工标注和保存继续可用。

下载源沿用桌面配置的 GitHub 发布文件，并支持桌面同样的 ModelScope 镜像：

~~~yaml
# 可选；不填写时读取 XANYLABELING_MODEL_HUB，否则默认 github。
model_hub: github  # 也可设为 modelscope
# 可选，连接/读取超时和单组件尝试次数。
download_timeout_seconds: 30
download_retries: 3
# 可选，只影响服务器上的模型下载；支持带用户名/密码的代理。
download_proxies:
  http: "YOUR_PROXY_ADDRESS"
  https: "YOUR_PROXY_ADDRESS"
~~~

下载发生在服务器，因此代理地址必须能从服务器访问。将示例代理替换为实际地址，
修改配置后重启服务即可生效。键 http/https 表示下载目标的协议；
HTTPS 下载也可以使用 http:// 开头的代理（通过 CONNECT 隧道）。
用户名或密码中的 @、:、# 等特殊字符需要 URL 编码。
代理凭据只填写在部署配置中，不要提交到仓库。

省略 download_proxies 或设为 null 时继续读取标准 http_proxy / https_proxy
环境变量（也支持大写形式，遵循 Python urllib 的规则）。
显式配置 download_proxies 后，以该映射替代环境变量代理，未列出的协议直连；
download_proxies: {} 表示禁用下载代理。仍遵循环境变量 no_proxy / NO_PROXY
中的绕过规则。代理仅用于模型下载，不会修改进程环境变量或全局网络设置。
文件校验值固定到已经验证过的模型版本，不会把损坏或被替换的下载当作可用模型。
浏览器只提交已登记的模型 ID，不接受任意下载地址或模型路径。

原有离线配置继续支持：不设置 preset，按 kind、attribute、files 登记本地文件。
也可以对 preset 的某个组件显式设置 files；显式路径会优先使用，不会被自动覆盖。
自定义权重必须配套字典。preset 的默认参数可由 parameters 覆盖。
旧版示例中的 /path/to/models/... 是占位路径，请替换为新示例的 preset 配置，
或移除 models 段使用内置默认列表。

整个服务共享一个槽位，OCR 三个子模型共同加载与卸载。
自动 GPU 在文件就绪后按最新设备状态选择，预热检查实际执行 provider；
失败不静默回退 CPU。显存需求和超时是初始配置，不构成资源预留。

空 HR 可以整图检测；已有文本框需选中后识别，只更新对应文字。
推理前保存草稿，结果通过权限、租约、图像和标注版本检查后应用。
任务期间继续编辑可能让结果过期；过期结果不覆盖新内容。
仅可取消自己的排队推理；卡死任务由超时处理，卸载以专用子进程退出为准。

## 分享、导出和恢复

所有者创建可查看或可编辑链接，明确选择数据集或单个样本，可设置到期时间。
fragment 令牌兑换 HttpOnly cookie 后从地址移除。
撤销使已有会话失效并释放租约；已经下载的内容无法收回。
HTTP 仅用于受信任内网；已有 HTTPS 入口时将 public_origin 配为 HTTPS。

导出冻结正式快照，后台生成独立版本目录和 ZIP，包含 annotations 的
HR/LR2/LR3/LR4、RealISRMeta.json 和版本清单。
imagePath 使用原始同名图像，imageData 为 null，不包含原图。
将 annotations 放进有同名四倍率图像的副本数据集，可由桌面版打开。
导出失败不发布半成品，也不覆盖上次成功输出。

~~~bash
# 服务运行时可在线备份，目标文件必须不存在。
python -m remote_labeling.backend.cli backup --config /path/to/remote.yaml --file /backup/realisr.db

# 恢复配置必须指向全新的空状态目录。
python -m remote_labeling.backend.cli restore --config /path/to/restored.yaml --file /backup/realisr.db
python -m remote_labeling.backend.cli serve --config /path/to/restored.yaml
~~~

恢复保留草稿和正式快照，旧租约、会话和操作重试凭据失效，
旧分享全部撤销，生成新所有者凭据；未完成任务中断，导出须重新生成。
数据库备份不含原图、root 下的标注 JSON、模型、外部配置和导出文件，
这些路径需继续可访问。写回进行中或尚待恢复时会拒绝生成数据库备份，请稍后重试。
运行中的数据库不能只复制主 .db 而忽略 WAL。

tmux 内运行 serve，用 Ctrl+C 正常停止。服务会先通知所有已连接的网页并主动结束
SSE 长连接，退出不再等待用户手动关闭网页。网页显示环形倒计时动画，默认 5 秒后
调用浏览器关闭标签页。可在部署配置中设置 `shutdown_countdown_seconds`（非负整数，
0 表示立即尝试关闭），配置示例见 `config.example.yaml`；省略时默认 5 秒。
倒计时在浏览器本地运行，不会延迟服务退出。浏览器可能禁止脚本关闭手动打开的
标签页，此时页面会提示手动关闭，服务仍正常退出。普通网络中断只会重连，不触发关页。
退出期间重复按 Ctrl+C 会继续等待正常清理，不会跳过推理服务和导出任务的收尾；
终端出现 `Application shutdown complete` 表示应用清理完成。
关闭浏览器不会卸载共享模型。
一个状态目录只允许一个服务实例，单 Uvicorn worker，不开 reload。
父进程意外退出时，推理子进程监测父进程并退出。

## 自动打开样本

进入工作台或切换数据集时，会按文件名自然顺序优先打开有待恢复草稿的样本，
其次是第一张未完成样本；全部完成则打开第一张。选择覆盖用户可访问的整个
数据集，列表自动定位到对应页。URL 已指定样本时优先打开该样本，刷新保持定位。
搜索、翻页、保存以及服务器状态更新不会重新选择当前样本。

首屏有待恢复草稿时直接预览草稿，仍需点击“开始编辑”申请编辑权。
其他人占用的样本可以查看。打开失败可点击“重试打开”或手动选择样本；
数据集扫描完成后才自动选择，没有可用样本时显示空状态。

草稿与正式结果相同时不获得草稿优先权。无正式结果时，有标注内容或在线保存
记录的样本才视为待恢复草稿。现有状态库无法区分“从未标注的空样本”和
“导入的空草稿”，后者仍按未完成样本参与选择。本功能无需数据库迁移或重新导入，
更新时需配套部署后端和构建后的前端。

## 升级保存流程

前端和后端需要配套更新并重新构建前端；旧 `/commit` 接口返回 410，提示刷新页面。
新增 `POST /api/v1/datasets/{dataset}/samples/{sample}/save-annotations` 接口。
数据库自动升级保留已有草稿、正式快照和导出记录，不会自动批量覆盖源 JSON。
打开样本时读取实际磁盘基线，需要同步的组会启用“保存标注”。

数据 root 现在是显式保存的输出位置；`state_dir`、`export_dir`、`cache_dir`
仍必须独立于 root。四份 JSON 无法由一次文件替换整体提交，因此服务使用持久化
写回记录和逐文件原子替换，失败回滚、重启恢复后才报告成功或继续写入。

## 开发与测试

Vite 开发模式运行 npm --prefix remote_labeling/frontend run dev，
将开发配置 public_origin 设为 Vite 的浏览器地址；API 监听 8765。
开发和正式配置均由部署者指定目录；development 不再强制要求 /tmp。

~~~bash
pytest -c remote_labeling/pyproject.toml remote_labeling/tests
npm --prefix remote_labeling/frontend test
npm --prefix remote_labeling/frontend run build
REALISR_PYTHON=/path/to/python npm --prefix remote_labeling/frontend run e2e
~~~

浏览器测试自动启动临时数据服务。Chromium 可使用系统 Chrome 或
REALISR_CHROME 指定路径；Firefox 需要 Playwright 配套浏览器：

~~~bash
cd remote_labeling/frontend
PLAYWRIGHT_BROWSERS_PATH=/tmp/realisr-playwright npx playwright install chromium firefox
~~~

从仓库根目录进行真实模型、负载验证：

~~~bash
REALISR_TEST_WEIGHTS=/path/to/models pytest -c remote_labeling/pyproject.toml remote_labeling/tests/integration/test_models.py
python -m remote_labeling.tests.performance.model_probe --config remote_labeling/config.example.yaml --weights /path/to/models --device auto --output /tmp/gpu.json
python -m remote_labeling.tests.performance.load --seconds 1800 --output /tmp/load.json
~~~

未提供权重时真实模型测试明确跳过；不能将跳过视为 GPU 验收通过。
负载脚本是合成本机 HTTP/存储基准，不替代真实内网图像下载、客户端反馈和人工试用。

更新接口类型后运行：

~~~bash
python -m remote_labeling.backend.cli openapi --config remote_labeling/config.example.yaml --file remote_labeling/openapi.json
npm --prefix remote_labeling/frontend run generate:api
npm --prefix remote_labeling/frontend run check:api
~~~

打包前先构建前端，再执行 python -m build remote_labeling。
发行包包含 SQL schema 和构建后的静态文件。运行数据、模型、node_modules
及测试临时产物不提交到 Git。


### 数据集统计看板

右侧“区域属性”上方提供紧凑统计概览，展开“各倍率详情”可查看
HR、LR2、LR3、LR4 的证据赋值进度及充分／模糊／不足／未设置分布。
看板标题右侧可切换“数据集 / 当前图像”。“当前图像”按画布正在显示的
草稿或正式标注统计，突出当前倍率的证据赋值比例，同时显示本组证据齐全区域数、
四倍率进度和剩余证据项。编辑、赋值、删除与撤销即时更新，未自动保存的修改会
明确标注；切换样本时显示加载状态，空组不会显示为 100% 完成。
当前图像进度使用已加载的画布数据，无需额外请求服务器。

样本和区域计数覆盖当前有权访问的全部样本，不受搜索或分页影响；
单样本分享只统计分享范围内的一个样本。

区域数、证据赋值和待保存草稿以服务器已保存内容为准，本地尚未自动保存的
修改会单独提示。证据齐全区域要求四个倍率均赋值，文本 HR 的默认赋值也计入。
已完成组数与左侧保持一致：再次编辑已完成组后，需重新保存标注才能恢复完成状态。
“待保存草稿”比较服务器草稿与正式快照，不代表逐次检查源文件的磁盘同步状态；
外部修改需要通过重新扫描反映。

看板复用现有事件通知及 5 秒同步周期；更新失败保留上次结果并提供局部重试，
扫描或校验失败会标明统计的适用状态。只读接口
`GET /api/v1/datasets/{dataset}/statistics` 在同一读取快照中完成鉴权和汇总，
按样本修订与导入版本缓存计数（最多 4096 个样本版本），不读取原图、不改写标注，
无需迁移现有状态库。

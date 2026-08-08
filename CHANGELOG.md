# Changelog

## Unreleased

- Source repository documentation: replaced the short README with a complete
  private-source guide covering download/package layout, first-run setup,
  foreground/background operation, language and resolution choices, ability
  reroll, troubleshooting, development entry points, and safe customization.
  The source remains private and excludes release archives, Chiaki binaries,
  logs, screenshots, and local runtime data.
- V131：重新以完整便携包模板制作 Windows 发行包，包含 Chiaki、驱动安装入口、全部启动脚本、说明文档和 V129 的主程序/依赖；不再沿用 V129 仅含程序目录的归档方式。
- V130：修正 V129 的发行归档范围。Windows 完整包重新包含便携 Chiaki、全部启动脚本、说明文档和自动化依赖；主程序及 `_internal` 使用 V129 的能力提升 OCR 性能优化构建。V129 不完整归档保留，仅用于追溯，不应下载测试。
- V129：能力提升重抽改为独立快速识别路径。界面语言锁定后只使用对应 OCR 模型；候选页、执行确认页、成功页在结构完整时不再重复全屏 OCR，执行确认页的“当前效果 + 执行”标记不再误触发增强图回退。最终覆盖结果和自动覆盖“是”高亮仍保留二次复核。能力提升状态机不再调用日文战斗结算高亮检测，避免额外 OCR 与跨流程输入干扰。
- V128：日文结算后的“再挑战确认”改用中央标题专用 OCR，实机 540P 截图可稳定识别 `再挑戦確認`。该弹窗优先于所有重战页、MSP、PS 按钮与金色检测，固定执行“上移到 はい -> 新帧确认高亮 -> Cross”。同时修复低分辨率重战页发送 Square 后只等待 OCR 的卡死问题：现在每轮持续检测红框金色，确认后立即进入受控 Cross 推进，不再等待三分钟后回城。
- V127：修复 V126 日文总评页误判为重战页的问题。`獲得MSP` 在总评页同样可能出现，现不再单独作为 Square 的页面依据；必须先检测到左下重战操作条，再以白色 PS 按钮或 MSP 作为补充确认。总评页将先等待并处理右下 `次へ`，避免第 1 场结算即提前发送 Square。
- V126：修复日文在每十场后的“再挑战确认”偶发未处理。`次へ` 后的确认过渡窗口现在优先于左下重战 Square 检测；即使低分辨率下弹窗标题 OCR 丢失，也会依据确认框的“是/否”高亮先完成选择和 Cross，不会把弹窗底层控件误当作重战页。同步校准 540P 日文确认框的两行高亮坐标。
- V125：统一中日文自动化的业务时序。简体中文作为结算、主城续战和能力提升重抽的唯一状态机基准；日文仅保留文字 OCR、低分辨率放大和局部视觉检测差异。日文自动重战确认开启后同样必须识别右下推进提示（`次へ` OCR 或视觉兜底）才发送 Cross，不再仅凭金色开关状态提前推进；同时删除前台结算循环中重复的日文低分辨率重战预检。新增日文十场“再挑战确认”弹窗优先级，确保先上移至 `はい` 再 Cross；并让结算页的受控等待分支也加入三分钟无倒计时巡检，确认回城后重新执行任务承接流程。
- V124：恢复简体中文结算的原有顺序。中文确认自动重战开启后不再直接发送 Cross，而是重新搜索右下“继续”并进行双帧确认；“确认后 Cross、必要时补发第二次 Cross”仅对日文续战流程生效，避免中文绕过“继续”识别。
- V123：结算切换后方案7索敌会立即丢弃正在进行的采样组，不再在结算页读取奥义、输出索敌日志或发送 L2。日文重战确认金色后先发送 Cross；若画面仍是已开启的重战页且尚未出现续战确认窗口，再补发一次 Cross，随后交由“是/否”高亮确认流程进入下一场。
- V122：修复自动重战已由 Square 开启并确认金色后没有继续发送 Cross 的问题。前台和后台结算循环现在会在首次确认自动重战已开启后等待一帧新画面，再发送一次受控 Cross 进入下一轮，并按每轮状态锁避免重复发送。
- V121：自动重战的默认启动阶段改为启动环境探测，不再以 `battle_wait` 作为控制面板启动或未知入口的默认值。主城、战斗和结算会先分类后再进入对应流程；Chiaki 暂时最小化、窗口重建或首帧不可用时保持探测并等待恢复，不再把主城误置于战斗等待或直接停止。
- V119：日文低分辨率结算页优先识别左下重战控制条中的白色圆形 PS5 按钮标记，识别区域缩小到固定按钮位置，不再依赖整条蓝色背景；若该标记因压缩或画面变化漏识别，再使用严格限定在 `獲得MSP` 行的 MSP+数字小区域作为兜底。所有日文 Square 入口统一经过这两个页面门槛，确认进入重战页后才允许发送，避免总评页误发方块。
- V118：修复日文结算状态机在总评页提前读取固定金色区域的问题。左下重战控制条未出现前，不读取重战 OCR/金色状态；确认金色后增加右下 `次へ` 视觉兜底，解决 540P OCR 漏识别导致金色确认后不发送 Cross。
- V117：将日文 540P 重战红框改为“扩大范围搜索、局部金色密度判断”，兼容图标轻微偏移，同时避免整块面板背景/金色细线造成误判；新增最佳局部占比和搜索偏移 DEBUG 日志。
- V116：针对日文 540P 结算流程强化页面推进。先识别左下重战控制页并发送一次 Square，再以红框对应的状态区域确认金色后才允许 Cross；图2 的 `次へ` 支持逐帧最多补发 3 次 Cross，直到进入重战页或出现其他确认页面。
- V115：修复日文低分辨率结算页被“继续/次へ”通用 OCR 分支提前处理的问题。现在先用左下重战控制条确认页面，发送一次 Square；只有左下状态确认变为金黄色后，才允许继续发送 Cross。中文流程和正常日文 OCR 流程保持原有路径。
- V114：补充实机 540P 重战控制页 OCR 变体 `再排製する/ロ事排殺する`，并在左下专用裁剪内允许稳定前缀 `再排`，修复 V113 仍停在重战页等待的问题。
- V113：重写日文重战页识别为左下半区文本检测；兼容实际截图中 `再挑戦する` 被 OCR 识别为 `再排殺する/回事排殺する` 的情况，避免窄行裁剪和字形误识别导致无法发送 Box。
- V112：区分日文结算总评页与重战控制页。总评页仅有 `次へ` 时先 Cross 进入下一页；检测到左下蓝色重战条后才强制执行 `再挑戦する` -> Box/Square -> 连续金色确认 -> Cross。
- V111：日文结算页自动重战改为严格闭环：识别 `再挑戦する` 后发送 Box/Square，必须连续确认左下状态图标变为金色才允许 Cross；`キャンセル/撤销` 文字变化不再单独放行。
- V110：撤回 V109 过宽的左下重战裁剪（会令单行 OCR 返回空文本），恢复实测可读出 `再挑戦する` 的窄行区域；日文结算在自动重战状态未确认前禁止发送右下 Cross，改为原地重试并记录等待原因。
- V109：修正日文结算页左下角 `再挑戦する` 的识别区域。旧区域位于按钮上方，导致既无法识别也不会发送 Box/Square；现在中日文共用左下底部区域，并同步修正日文 OCR 兜底裁剪。
- V108：进一步修复日文结算页被中央 `挑战确认/リザルト確認` OCR 分支提前处理的问题；进入日文结算相关分支前先预检底部自动重战控件，确保先完成 Box/Square 状态检测和连续确认，再处理 Cross。
- V107：修复日文结算页的通用蓝色高亮兜底抢先发送 Cross 的问题。现在会先检查并验证 Box/Square 自动重战状态，确认自动重战已开启后才允许处理结算确认高亮，避免跳过自动重战流程后只能依赖回城兜底。
- V106：修复日文结算页 `再挑戦する` 位于底部中央时无法识别的问题；新增日文中央底部 OCR 兜底，并继续沿用 Box 开启、连续验证自动重战后才推进结算的流程。
- V103：窗口移动不再被误判为客户区尺寸变化，不会反复暂停识别/拦截 Cross；真正切换 360P/540P/720P/1080P 后仍短暂等待新画面稳定。
- V103：Windows 发行版取消 UPX 压缩，降低安全软件的启发式误报风险；主程序继续保留自动重战所需的管理员权限和 UAC 流程。
- 新增独立索敌方案下拉选择：保留原“近战/法系方案”，并加入“远程索敌方案”；远程方案仅在主控奥义条满格且上下左右四个技能连续 15 秒无变化时触发转身索敌，可在设置页点击“应用索敌方案”。
- V65 实机验证：Chiaki 串流窗口意外关闭后，工具能够自动检测窗口丢失、重新打开串流，并继续自动重战流程。
- V41 能力提升星数回填改为使用游戏 1★~10★ 精确数值表；修正百分比/昏厥值、攻击力和 HP 的非线性档位，未知中间值不再被公式插值。
- V40 修复能力提升突破页记忆焦点停在“取消”时只发送 Cross 导致流程停住的问题；每轮先定位“执行”再确认。
- V39 能力提升记录加入独立历史清除功能；星数识别支持独立 OCR 星号行、属性数值回填和历史交叉验证，回填后再计算总星数。
- V31 实机修复：任务中心的任务选择、难度、匹配设置页面全部只发送 Cross；
  只有 OCR 确认 `已承接任务/受注しました` 后才发送一次 Square 打开准备界面。
- 战斗 HUD 判定改为“跳跃标记 + 技能 HUD 结构 + 右半屏剩余时间/残り時間 OCR”并确认两帧，
  修复日文界面进入战斗后停在 `battle_wait`、完全不操作的问题。
- 卡死监控加入轻微画面抖动对齐、滚动低活动比例和历史平均/最长战斗耗时组合判据。
- 一键重连现在通过 Chiaki Discovery 协议先查询 PS5 的 `standby/ready` 状态。
- 检测到 `standby` 时直接发送 PS5 唤醒包，并确认变为 `ready` 后才启动串流，避免持续打开黑屏窗口。
- 不再依赖便携版 Chiaki 未启用的 `wakeup` CLI 子命令。
- 自动结束配置文件尚未创建时不再每秒重复输出读取失败警告。
- 重连后若进入“主菜单”或“持有物”等游戏菜单，会用 Moon 逐层返回正常画面，不再误发 Cross 或 L2。
- 串流恢复后的固定输入由两次 Cross 修正为两次 Moon（前台 `Backspace`、后台 DS4 Circle），并逐次等待新画面确认按键已送达。
- 主界面新增“重新捕获串流窗口（F4）”按钮；自动识别失败时，用户可在 Chiaki 串流窗口已打开后手动触发重新枚举和后台捕获绑定。
- 串流窗口标题旁新增“应用标题”按钮；修改标题后可立即保存，运行中会自动触发一次 F4 捕获。

All notable user-facing changes are recorded here. The public version number
uses semantic versioning; the older local build number is retained for support.

## Unreleased - reconnect reliability

- “一键重连并挂机”现在会先自动停止旧自动化并释放输入，不再要求用户手动停止。
- 重连改为可中断的多次尝试：按当前绑定 PID 关闭失败串流、发送 PS5 唤醒包、等待连续新帧，并在 `Unknown Session Request Error` 后自动重开。
- Cross 只在串流持续输出新画面后发送；未恢复到战斗、结算或主城时会重新确认并重试，成功后直接进入与 F1 相同的完整挂机主流程。
- 后台 Chiaki 误最小化时暂停画面判断；恢复窗口后自动重建捕获，不再要求重开工具或串流。
- Chiaki 路径新增自动查找和文件浏览入口，选择结果仅保存在本机；主机注册键只从 Chiaki 注册表临时读取用于唤醒，不写入日志、设置或发布文件。

## v0.1.0 - 2026-08-01 (Windows build V26)

- Unified the Chiaki launcher, automation controls, background-mode checks,
  PSN AccountID helper, and live log in one Windows interface.
- Added Windows Graphics Capture and virtual DualShock 4 background operation.
- Added automatic rebattle, verified result-screen continuation, and battle-only
  target recovery using the skill-state timer, turn/search arc, and one L2 press.
- Added `F3` pause/resume with immediate release of all automation inputs.
- Added session battle counts, per-battle duration statistics, and stop limits by
  battle count, elapsed minutes, or daily clock time.
- Reduced capture and OCR load, disabled ONNX idle spinning, and fixed mixed
  Windows code-page output in packaged logs.
- Added Chiaki window-recreation detection and automatic background-capture
  rebinding.

## Publishing status

This is a private release candidate, not authorization for public
redistribution. See `PUBLISH_BLOCKERS.md` before publishing source or binaries.

## Post-V26 maintenance

- Replaced the ambiguous automatic-stop labels with an explicit applyable
  settings panel: completed battles, runtime in minutes, and local-clock close
  time (`HH:MM`). Changes can be applied while a run is active.
- Removed the skill-monitor detail switch and its high-frequency diagnostic
  lines; detection remains enabled internally.
- Added clear background-mode lock text explaining that stopping the run and
  clearing the checkbox switches back to foreground operation.
- Simplified the Chiaki mapping dialog to list only the keys that must be
  changed.
- Reduced statistics persistence and GUI refresh work from sub-second polling to
  one-second updates, and removed a duplicate statistics write per cycle.
- Clarified runtime-limit semantics: the run timer starts when the automation
  process starts, while each battle's duration remains a separate statistic.
- Fixed all Windows launcher scripts to locate the current root EXE and provide
  a readable error when the package was not fully extracted.
- Added independent optional ViGEmBus and HidHide install buttons and package
  launchers. HidHide is never required by the background check and is never
  configured automatically.
- Fixed pause/resume race conditions: F3 resume now releases all inputs,
  briefly blocks forward movement, and rechecks the real `继续` and
  `再次挑战/撤销` result controls before the movement worker can resume.
- Removed the previously added upper-left result-heading crop. Battle-end and
  restart recovery now use only the existing result controls shown in the
  captured gameplay.
- Reduced perceived log startup latency: launcher status is displayed
  immediately, child output is unbuffered, polling runs every 250 ms, and the
  heavy OCR import happens after a visible progress message.
- Added one-click input synchronization. Foreground mode reads Chiaki 2.2.0's
  QSettings registry keymap and merges upstream defaults; background mode
  verifies the ViGEm virtual DS4 and preserves the saved axis-direction option.
  Existing Chiaki mappings are no longer overwritten or needlessly replaced.
- Added optional frozen-stream recovery with a saved Chiaki executable path,
  host nickname, and host address. Recovery closes only the currently bound
  stream process and binds the replacement process by PID.
- Recovery now separates battle HUD, result screens, animations/loading, and
  town recovery. After reconnecting, a stable unknown screen receives one
  verified L2 town probe; only a confirmed `任务中心` menu starts the town
  quest macro. Recovery failures release all input instead of continuing W.
- Added a one-click reconnect-and-run action. It performs the same verified
  recovery route and, on success, continues into the full automation loop with
  session statistics, stop conditions, pause/resume, and later freeze recovery.
- The freeze timeout now accepts decimal minutes for deliberate testing (for
  example `0.2` minutes). Values below two minutes require confirmation, while
  ten minutes remains the recommended unattended setting.

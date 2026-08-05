# Changelog

## Unreleased

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

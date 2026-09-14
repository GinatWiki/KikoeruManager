# 库存管理「文件浏览 / 索引」重构方案

> 状态：**待实施**（本文件是实施契约，逐阶段落地；每阶段附验收标准与回滚点）
> 起因：①选择库存后不加载文件库内容；②本地文件被处理后，「刷新」「刷新本页索引」显示的都不是实际文件。
> 硬约束：**必须兼容所有使用/操作库存的现有功能**（清单见 §2）。

---

## 1. 现状与根因

### 1.1 后端索引子系统（`backend/app/core/library_index/`）

| 文件 | 行数 | 职责 |
|---|---|---|
| `snapshot_store.py` | 3940 | 快照 + generation |
| `mutation_service.py` | 2631 | 变更队列（写路径） |
| `service.py` | 2053 | 对外服务 |
| `watcher_driver.py` | 732 | 目录监听 / generation recovery |
| `local_scanner.py` / `remote_scanner.py` | 340 / 400 | 扫描 |
| `fts.py` | 293 | 全文检索 |
| `materializer_db.py` / `types.py` | 94 / 93 | 落库 / 类型 |

### 1.2 根因（结构性）

1. **读入口分散**：`/api/library/browser/files`、`/api/library/library/files`、`/api/library/index/search`、`/api/library/folder-contents`、`/api/library/browser/list-folders`、`/api/library/browser/navigation-snapshot` 各走各的数据源，**索引快照与实时目录并存且无统一裁决**。
2. **generation 与「页面看到什么」没有绑定**：`[索引维护] 过期 generation 清理`、`[索引 watcher] generation recovery`、`[LocalScanner] 子树扫描` 都在跑，但页面读到的可能是旧快照。
3. **写入不失效**：变更走 mutation 增量，阻塞/失败（`mutations/retry-blocked`）时页面仍是旧内容 → 「处理完看到的不是实际文件」。
4. **刷新语义混淆**：「刷新」只是重拉接口（还是旧快照）；「刷新本页索引」是重建（慢且不一定覆盖当前层）。

---

## 2. 兼容清单（重构必须覆盖）

**利好：前端所有库存端点都收敛在单一网关 `frontend/src/api/index.js`（34 个片段）→ 改 wire format 只需改一处 + 各消费者。**

### 2.1 强耦合（与浏览/索引直接相关，需同步验证）

| 前端 | 能力 | 处理方式 |
|---|---|---|
| `views/Library.vue` | libraries / folder-content(s) / folder-completion / batch-delete / auto-circle-group / open-folder | 重构主战场：切到新 listing；变更后走失效 |
| `components/library/LibrarySearchOverlay.vue` | index/search、index/global-search | **契约冻结**，内部改走同一数据源 |
| `components/library/FolderContentsDialog.vue` | mojibake-preview | 复用「列一层目录」 |
| `components/library/LibraryFolderCompletionDialog.vue` | folder-completion | 复用「列目录 / 统计」 |
| `components/library/LibraryMoveDialog.vue` | libraries | 复用「库存 / 目录树」 |
| `components/common/RemoteFolderPickerDialog.vue` | browser/files | 复用「列一层目录」 |

### 2.2 只读库存列表（仅 `libraries`，契约不变即零风险）

`views/ASMRSync.vue`、`views/CircleCompletion.vue`、`views/DuplicateCheck.vue`、`components/circle/CircleDownloadPreviewDialog.vue`、`components/circle/CircleLocalUploadDialog.vue`、`components/common/ServerUploadPreviewDialog.vue`、`components/FileUploader.vue`、`components/settings/LibraryInventoryPanel.vue`、`components/settings/StorageSettingsPanel.vue`、`composables/useSettingsDraft.js`、`composables/useSynologyProfiles.js`

### 2.3 其它

`stores/libraryIndexState.js`（索引状态/generation 语义要保留）、`App.vue`（library-backup / mojibake-preview）。

### 2.4 冻结的接口契约（只增不改）

`/api/library/libraries`、`/api/library/index/search`、`/api/library/index/global-search`、`/api/library/index/status`、`/api/library/folder-completion/*`、`/api/library-backup/*`。

---

## 3. 设计契约（6 条）

1. **单一读入口**：`GET /api/library/browser/listing?library_id=&relative_path=&cursor=&mode=`，旧端点（browser/files、library/files、folder-contents、list-folders）**保留为薄适配器**，内部调用同一实现。
2. **索引是缓存、目录是真相**：`mode=index`（默认，快）读快照；`mode=verify` 只对**当前层**做浅扫（一层 stat，不做全树），返回差异并触发该子树增量重建。
3. **路径即主键**：`(library_id, relative_path)`；不使用名称/序号拼接 id。
4. **统一序列化层**：条目字段固定为
   `{ name, is_dir, size, mtime, relative_path, child_count, stale, source }`，
   响应带 `{ source, generation, fresh_at, cursor, has_more }`。
5. **写入即精确失效**：所有 mutation（解压入库 / 归档 / 重命名 / 删除 / 移动 / 建目录 / 批量）完成后失效受影响子树，下次请求该层自动 verify。
6. **准确性护栏 + 性能**：条目带 `(size, mtime, inode)`；verify 不一致只标单项 `stale`；浏览=每层一查+游标分页；搜索= FTS；**任何路径都不同步全树扫描**。

---

## 4. 分阶段实施

### 阶段 1：新 listing 服务 + 适配器（不改前端也能跑）
- 实现 `listing`（index 模式）+ 统一序列化层；旧端点改为薄适配器。
- 验收：6 个强耦合消费者行为不变（回归 + 手工验证）；`libraries`/`search`/`status` 契约未变。
- 回滚点：路由级开关，可切回旧实现（保留旧函数体）。

### 阶段 2：verify 模式与刷新语义
- `mode=verify` 浅扫本层 + 差异标记；前端「刷新本页」改走 verify，「重建索引」保留为后台任务。
- 验收：本地文件被处理后，刷新本页立即显示真实内容（用一个真机场景验证）。

### 阶段 3：写入即失效
- 在 mutation 完成回调中失效受影响子树；`browser/rename|delete|move|create-folder|batch-*` 全覆盖。
- 验收：处理/移动/重命名后无需手动刷新即拿到真实内容；无全树扫描。

### 阶段 4：前端收口与性能验证
- `Library.vue` 切换到新 listing；其余 5 个消费者改用同一序列化层。
- 验收：大库存（≥1 万条目）下列目录 p95 < 300ms、搜索保持 FTS 响应；`stores/libraryIndexState.js` 状态语义不变。

---

## 5. 风险与回滚

| 风险 | 对策 |
|---|---|
| 旧端点被外部/前端其它路径使用 | 适配器保留原响应结构；先做契约快照测试再改内部 |
| 失效粒度太粗导致频繁重建 | 只失效受影响子树（按 relative_path 前缀），不整库失效 |
| verify 浅扫在某些库不可用（远端/网络盘） | verify 失败自动回落 index 模式并在响应里标 `verify_failed`，不阻塞页面 |
| `snapshot_store.py` 体量大 | 本方案不动其内部实现，只在 service 层新增 listing/verify，降低回归面 |

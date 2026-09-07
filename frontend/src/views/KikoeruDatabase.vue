<template>
  <div class="kikoeru-db-page max-w-[1400px] mx-auto px-4 sm:px-6 lg:px-8 py-6 space-y-6">
    <AppPageHeader
      :icon="Database"
      icon-color="var(--km-nav-subtitle-icon, #6366f1)"
      title="Kikoeru 数据库"
      subtitle="直接管理远程 Kikoeru 的 SQLite 数据库：浏览、编辑、备份恢复与一键套用文件命名。"
    >
      <button class="page-head-btn ghost btn-diagnose" type="button" :disabled="diagnosing" @click="runDiagnose">
        <Loader2 v-if="diagnosing" :size="13" :stroke-width="2.4" class="animate-spin" />
        <Stethoscope v-else :size="13" :stroke-width="2.4" class="page-head-btn-icon" />
        <span class="page-head-btn-label">{{ diagnosing ? '检测中…' : '检测数据库' }}</span>
      </button>

      <button class="page-head-btn ghost btn-backup" type="button" :disabled="backingUp" @click="doBackup">
        <Loader2 v-if="backingUp" :size="13" :stroke-width="2.4" class="animate-spin" />
        <Save v-else :size="13" :stroke-width="2.4" class="page-head-btn-icon" />
        <span class="page-head-btn-label">{{ backingUp ? '备份中…' : '立即备份' }}</span>
      </button>

      <button
        class="page-head-btn ghost is-blue btn-rename"
        type="button"
        :disabled="!featureEnabled"
        @click="openRenameWizard(false)"
      >
        <Wand2 :size="13" :stroke-width="2.6" class="page-head-btn-icon" />
        <span class="page-head-btn-label">一键套用文件命名</span>
      </button>

      <button
        class="page-head-btn ghost is-blue btn-rating-fix"
        type="button"
        :disabled="!featureEnabled"
        @click="openRatingFixWizard(false)"
      >
        <Star :size="13" :stroke-width="2.6" class="page-head-btn-icon" />
        <span class="page-head-btn-label">评分修复</span>
      </button>

      <button class="page-head-btn ghost btn-refresh icon-only" type="button" title="刷新" @click="refreshAll">
        <RefreshCw :size="13" :stroke-width="2.6" class="page-head-btn-icon" />
      </button>
    </AppPageHeader>

    <!-- 连接状态卡 -->
    <section class="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
      <div class="px-6 py-4 flex flex-wrap items-center gap-x-8 gap-y-2">
        <div class="flex items-center gap-2">
          <span class="inline-block size-2 rounded-full" :class="featureEnabled ? 'bg-emerald-500' : 'bg-slate-300'" />
          <span class="text-sm text-slate-700">{{ featureEnabled ? '功能已启用' : '功能未启用（请在设置中开启）' }}</span>
        </div>
        <div class="text-sm text-slate-500">
          数据库：<code class="text-xs bg-slate-100 rounded px-1.5 py-0.5">{{ dbPath || '未配置' }}</code>
        </div>
        <div class="text-sm text-slate-500">
          扫描监听：
          <el-tag v-if="scanStatus.listening" type="success" size="small" effect="plain">监听中</el-tag>
          <el-tag v-else type="info" size="small" effect="plain">未运行</el-tag>
          <span v-if="scanStatus.last_scan_finished_at" class="ml-2 text-xs text-slate-400">
            上次扫描完成 {{ scanStatus.last_scan_finished_at }}
          </span>
        </div>
      </div>
    </section>

    <!-- 数据表浏览 -->
    <section class="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
      <div class="px-6 pt-4">
        <el-tabs v-model="activeTable">
          <el-tab-pane v-for="t in tables" :key="t.name" :name="t.name">
            <template #label>
              <span class="inline-flex items-center gap-1.5">
                {{ t.name }}
                <el-tag v-if="t.mode === 'readonly'" size="small" type="warning" effect="plain">只读</el-tag>
              </span>
            </template>
          </el-tab-pane>
        </el-tabs>
      </div>

      <div class="px-6 pb-2 flex flex-wrap items-center justify-between gap-3">
        <div class="flex items-center gap-2">
          <el-input
            v-model="searchText"
            placeholder="搜索文本字段…"
            clearable
            class="!w-72"
            @keyup.enter="loadRows"
            @clear="loadRows"
          />
          <el-button :icon="Search" @click="loadRows">搜索</el-button>
        </div>
        <div class="flex items-center gap-2">
          <el-button
            v-if="currentMode === 'editable' && selectedRows.length > 0"
            type="primary"
            plain
            :icon="Wand2"
            @click="openRenameWizard(true)"
          >
            套用命名（已选 {{ selectedRows.length }} 行）
          </el-button>
          <el-button v-if="currentMode === 'editable'" :icon="Plus" @click="openCreateDialog">新增行</el-button>
          <el-button
            v-if="currentMode === 'editable'"
            type="danger"
            plain
            :disabled="selectedRows.length === 0"
            :icon="Trash2"
            @click="deleteSelected"
          >
            删除选中
          </el-button>
        </div>
      </div>

      <el-table
        :data="rows"
        v-loading="loadingRows"
        size="small"
        border
        stripe
        class="w-full"
        @selection-change="onSelectionChange"
      >
        <el-table-column v-if="currentMode === 'editable'" type="selection" width="42" />
        <el-table-column
          v-for="col in displayColumns"
          :key="col.name"
          :prop="col.name"
          :label="col.name"
          :min-width="columnWidth(col)"
          show-overflow-tooltip
        >
          <template #default="{ row }">
            <span class="text-xs">{{ renderCell(row[col.name]) }}</span>
          </template>
        </el-table-column>
        <el-table-column v-if="currentMode === 'editable'" label="操作" width="80" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openEditDialog(row)">编辑</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="px-6 py-4 flex justify-end">
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :total="total"
          :page-sizes="[20, 50, 100, 200]"
          layout="total, sizes, prev, pager, next, jumper"
          @current-change="loadRows"
          @size-change="loadRows"
        />
      </div>
    </section>

    <!-- 备份管理卡 -->
    <section class="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
      <div class="px-6 py-4 border-b border-slate-100 flex items-center justify-between bg-slate-50/50">
        <h2 class="text-base font-semibold text-slate-900">备份与恢复</h2>
        <div class="flex items-center gap-2">
          <el-button size="small" :icon="RefreshCw" @click="loadBackups">刷新</el-button>
          <el-button size="small" type="danger" plain :icon="Undo2" @click="restoreDialogVisible = true">一键恢复</el-button>
        </div>
      </div>
      <div class="px-6 py-3 text-xs text-slate-500 space-y-1">
        <p>· <b>原始备份</b>（activate）：功能激活时自动创建，永久保留；<b>自动备份</b>（auto）按设定间隔滚动；<b>手动备份</b>（manual）不清理。</p>
        <p>· 每次编辑/删除/新增前都会自动拍<b>回滚快照</b>（snapshot，折叠区可查看）；恢复前也会自动生成 pre-restore 备份兜底。</p>
      </div>
      <el-table :data="backups" size="small" border class="w-full" max-height="320">
        <el-table-column prop="filename" label="文件名" min-width="260" show-overflow-tooltip />
        <el-table-column label="类型" width="120">
          <template #default="{ row }">
            <el-tag :type="kindTagType(row.kind)" size="small" effect="plain">{{ kindLabel(row.kind) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="时间" width="180" />
        <el-table-column label="大小" width="110">
          <template #default="{ row }">{{ formatSize(row.size) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="90">
          <template #default="{ row }">
            <el-button link type="danger" size="small" @click="confirmRestore(row)">恢复到此</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="px-6 py-2">
        <el-collapse>
          <el-collapse-item :title="`回滚点（写前快照，保留最近 ${snapshotCount} 份）`">
            <el-table :data="snapshots" size="small" border class="w-full" max-height="240">
              <el-table-column prop="filename" label="文件名" min-width="260" show-overflow-tooltip />
              <el-table-column prop="created_at" label="时间" width="180" />
              <el-table-column label="操作" width="90">
                <template #default="{ row }">
                  <el-button link type="danger" size="small" @click="confirmRestore(row)">回滚到此</el-button>
                </template>
              </el-table-column>
            </el-table>
          </el-collapse-item>
        </el-collapse>
      </div>
    </section>

    <!-- 编辑对话框 -->
    <el-dialog v-model="editDialogVisible" :title="editingId != null ? `编辑行 ${editLabel}` : '新增行'" width="720px" destroy-on-close>
      <el-form label-position="top" class="grid grid-cols-1 md:grid-cols-2 gap-x-6">
        <el-form-item
          v-for="col in editColumns"
          :key="col.name"
          :label="col.name + (col.pk ? '（主键）' : '')"
          :class="col.type.includes('TEXT') || col.type.includes('JSON') ? 'md:col-span-2' : ''"
        >
          <el-switch
            v-if="isBoolColumn(col)"
            v-model="editForm[col.name]"
          />
          <el-input-number
            v-else-if="isIntColumn(col)"
            v-model="editForm[col.name]"
            :controls="false"
            class="!w-full"
          />
          <el-input
            v-else-if="col.type.includes('TEXT') || col.type.includes('JSON') || isLongValue(editForm[col.name])"
            v-model="editForm[col.name]"
            type="textarea"
            :rows="4"
          />
          <el-input v-else v-model="editForm[col.name]" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="savingRow" @click="saveRow">保存</el-button>
      </template>
    </el-dialog>

    <!-- 恢复选择对话框 -->
    <el-dialog v-model="restoreDialogVisible" title="一键恢复" width="760px" destroy-on-close>
      <el-alert type="warning" :closable="false" class="mb-3" show-icon
                title="恢复会用所选备份整文件替换当前数据库。执行前系统会自动生成 pre-restore 备份；如 Kikoeru 正在写入可能失败，建议先停止 Kikoeru。" />
      <el-table :data="restoreCandidates" size="small" border max-height="380" highlight-current-row @current-change="r => (restoreSelection = r)">
        <el-table-column label="" width="50">
          <template #default="{ row }">
            <el-radio :model-value="restoreSelection?.filename" :value="row.filename"><span /></el-radio>
          </template>
        </el-table-column>
        <el-table-column prop="filename" label="文件名" min-width="240" show-overflow-tooltip />
        <el-table-column label="类型" width="110">
          <template #default="{ row }">
            <el-tag :type="kindTagType(row.kind)" size="small" effect="plain">{{ kindLabel(row.kind) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="时间" width="170" />
        <el-table-column label="大小" width="100">
          <template #default="{ row }">{{ formatSize(row.size) }}</template>
        </el-table-column>
      </el-table>
      <template #footer>
        <el-button @click="restoreDialogVisible = false">取消</el-button>
        <el-button type="danger" :disabled="!restoreSelection" :loading="restoring" @click="doRestore">
          恢复到此备份
        </el-button>
      </template>
    </el-dialog>

    <!-- 一键套用命名向导 -->
    <el-dialog v-model="renameWizardVisible" title="一键套用文件命名" width="980px" destroy-on-close>
      <el-alert type="info" :closable="false" class="mb-3" show-icon
                title="将 t_work.title 重命名为文件夹名中的 work_name 内容（不含 [] 括号段），并置 is_custom_meta=1 防止 Kikoeru 重新抓取时覆盖。" />
      <div v-if="renamePreview" class="mb-3 text-sm text-slate-600">
        共 <b>{{ renamePreview.total }}</b> 行：可改名 <b class="text-emerald-600">{{ renamePreview.changed }}</b>，
        跳过 <b class="text-amber-600">{{ renamePreview.skipped }}</b>（未按模板命名/无 RJ 号/无变化）
      </div>
      <el-table v-if="renamePreview" :data="renamePreview.items" size="small" border max-height="420">
        <el-table-column prop="id" label="RJ/VJ" width="120" />
        <el-table-column prop="dir" label="文件夹名" min-width="220" show-overflow-tooltip />
        <el-table-column prop="old_title" label="当前标题" min-width="200" show-overflow-tooltip />
        <el-table-column label="新标题" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <span :class="row.changed ? 'text-emerald-600 font-medium' : 'text-slate-400'">
              {{ row.changed ? row.new_title : '（不变/跳过）' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag v-if="row.changed" type="success" size="small" effect="plain">改名</el-tag>
            <el-tag v-else type="warning" size="small" effect="plain">跳过</el-tag>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="renamePreview && renamePreview.items.length > previewLimit" class="mt-2 text-xs text-slate-400">
        仅显示前 {{ previewLimit }} 行预览，执行时按全部 {{ scopeLabel }} 处理。
      </div>
      <template #footer>
        <el-button @click="renameWizardVisible = false">取消</el-button>
        <el-button :loading="renamePreviewing" @click="loadRenamePreview">刷新预览</el-button>
        <el-button
          type="primary"
          :disabled="!renamePreview || renamePreview.changed === 0"
          :loading="renameApplying"
          @click="doApplyRename"
        >
          执行（{{ renamePreview?.changed || 0 }} 行）
        </el-button>
      </template>
    </el-dialog>

    <!-- 评分修复向导 -->
    <el-dialog v-model="ratingFixVisible" title="评分修复" width="1000px" destroy-on-close>
      <el-alert type="info" :closable="false" class="mb-3" show-icon
                title="对评分为 0 的作品用本项目 DLsite 元数据重抓一次；仍为 0 时自动抓取其他版本评分（优先日文原版，其次其他翻译版）。回填字段：评分、评分人数、评分分布、销量、评价数、价格。" />
      <div v-if="ratingFixPreview" class="mb-3 text-sm text-slate-600">
        共 <b>{{ ratingFixPreview.total }}</b> 行（0 分 + 满分复核）：可修复
        <b class="text-emerald-600">{{ ratingFixPreview.fixable }}</b>
        （本体重抓 {{ ratingFixPreview.own }}，其他版本 {{ ratingFixPreview.linked }}），
        确实无评分 <b class="text-slate-500">{{ ratingFixPreview.none }}</b>，
        <template v-if="ratingFixPreview.error > 0">
          <b class="text-red-500">网络失败 {{ ratingFixPreview.error }}</b>（可点下方「重试失败项」）
        </template>
      </div>
      <el-table v-if="ratingFixPreview" :data="ratingFixPreview.items" size="small" border max-height="420">
        <el-table-column prop="id" label="RJ/VJ" width="110" />
        <el-table-column label="目标" width="90">
          <template #default="{ row }">
            <el-tag :type="row.target_kind === 'perfect' ? 'warning' : 'info'" size="small" effect="plain">
              {{ row.target_kind === 'perfect' ? '满分复核' : '0 分' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="title" label="标题" min-width="180" show-overflow-tooltip />
        <el-table-column label="当前" width="90">
          <template #default="{ row }">{{ row.current_rate_average_2dp ?? 'NULL' }}</template>
        </el-table-column>
        <el-table-column label="修复来源" width="200">
          <template #default="{ row }">
            <template v-if="row.plan === 'own'">
              <el-tag type="success" size="small" effect="plain">本体重抓</el-tag>
            </template>
            <template v-else-if="row.plan === 'linked'">
              <el-tag type="primary" size="small" effect="plain">{{ row.source_lang || '其他版本' }}</el-tag>
              <span class="ml-1 text-xs">{{ row.source_rjcode }}</span>
              <span v-if="row.source_work_type === 'original'" class="ml-1 text-xs text-slate-400">(原版)</span>
            </template>
            <template v-else-if="row.plan === 'error'">
              <el-tag type="danger" size="small" effect="plain">网络失败</el-tag>
            </template>
            <span v-else class="text-xs text-slate-400">-</span>
          </template>
        </el-table-column>
        <el-table-column label="新评分" width="130">
          <template #default="{ row }">
            <span v-if="row.fix" class="text-emerald-600 font-medium">
              {{ row.fix.rate_average_2dp }}（{{ row.fix.rate_count }} 评）
            </span>
            <span v-else-if="row.plan === 'error'" class="text-red-400 text-xs">待重试</span>
            <span v-else class="text-slate-400 text-xs">不可修复</span>
          </template>
        </el-table-column>
        <el-table-column prop="reason" label="说明" min-width="170" show-overflow-tooltip />
      </el-table>
      <div class="mt-3">
        <el-input-number v-model="ratingFixLimit" :min="10" :max="2000" :step="50" size="small" />
        <span class="ml-2 text-xs text-slate-500">单次处理上限（0 分作品较多时按 id 顺序分批）</span>
      </div>
      <template #footer>
        <el-button @click="ratingFixVisible = false">取消</el-button>
        <el-button :loading="ratingFixPreviewing" @click="loadRatingFixPreview">刷新预览</el-button>
        <el-button
          v-if="ratingFixErrorIds.length > 0"
          type="warning" plain
          :loading="ratingFixRetrying"
          @click="retryRatingFixErrors"
        >
          重试失败项（{{ ratingFixErrorIds.length }}）
        </el-button>
        <el-button
          type="primary"
          :disabled="!ratingFixPreview || ratingFixPreview.fixable === 0"
          :loading="ratingFixApplying"
          @click="doApplyRatingFix"
        >
          执行（{{ ratingFixPreview?.fixable || 0 }} 行）
        </el-button>
      </template>
    </el-dialog>

    <!-- 检测结果对话框 -->
    <el-dialog v-model="diagnoseDialogVisible" title="数据库检测结果" width="640px">
      <div class="space-y-3">
        <div v-for="(s, i) in diagnoseResult?.steps || []" :key="i" class="flex items-start gap-2">
          <CheckCircle2 v-if="s.ok" :size="16" class="text-emerald-500 mt-0.5 shrink-0" />
          <XCircle v-else :size="16" class="text-red-500 mt-0.5 shrink-0" />
          <div>
            <div class="text-sm font-medium text-slate-800">{{ s.step }}</div>
            <div class="text-xs text-slate-500 break-all">{{ s.detail }}</div>
          </div>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onActivated, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  CheckCircle2,
  Database,
  Loader2,
  Plus,
  RefreshCw,
  Save,
  Search,
  Star,
  Stethoscope,
  Trash2,
  Undo2,
  Wand2,
  XCircle
} from 'lucide-vue-next'
import { configApi, kikoeruDbApi } from '../api'
import AppPageHeader from '../components/common/AppPageHeader.vue'

// ---- 状态 ----
const tables = ref([])
const activeTable = ref('')
const rows = ref([])
const columns = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(50)
const searchText = ref('')
const loadingRows = ref(false)
const selectedRows = ref([])

const dbPath = ref('')
const featureEnabled = ref(false)
const scanStatus = ref({ listening: false, last_scan_finished_at: '', checkpoint: '' })

const backups = ref([])
const snapshots = ref([])
const backingUp = ref(false)
const restoreDialogVisible = ref(false)
const restoreSelection = ref(null)
const restoring = ref(false)

const editDialogVisible = ref(false)
const editingRow = ref(null)
const editForm = ref({})
const savingRow = ref(false)

const renameWizardVisible = ref(false)
const renamePreview = ref(null)
const renamePreviewing = ref(false)
const renameApplying = ref(false)
const renameScopedToSelection = ref(false)
const previewLimit = 300

// ---- 评分修复 ----
const ratingFixVisible = ref(false)
const ratingFixPreview = ref(null)
const ratingFixPreviewing = ref(false)
const ratingFixApplying = ref(false)
const ratingFixRetrying = ref(false)
const ratingFixScopedToSelection = ref(false)
const ratingFixLimit = ref(300)

const ratingFixErrorIds = computed(() =>
  (ratingFixPreview.value?.items || []).filter(i => i.plan === 'error').map(i => i.id)
)

function mergeRatingFixItems(newItems) {
  // 原位替换重试成功的行（重试结果可能是 error→own/linked/none 的任意转换）
  const byId = new Map((ratingFixPreview.value?.items || []).map(i => [i.id, i]))
  for (const item of newItems || []) byId.set(item.id, item)
  const items = [...byId.values()]
  ratingFixPreview.value = {
    total: items.length,
    fixable: items.filter(i => i.plan !== 'none' && i.plan !== 'error' && i.fix).length,
    own: items.filter(i => i.plan === 'own').length,
    linked: items.filter(i => i.plan === 'linked').length,
    none: items.filter(i => i.plan === 'none').length,
    error: items.filter(i => i.plan === 'error').length,
    items
  }
}

async function retryRatingFixErrors() {
  if (ratingFixErrorIds.value.length === 0) return
  ratingFixRetrying.value = true
  try {
    const result = await kikoeruDbApi.ratingFixPreview({
      ids: ratingFixErrorIds.value,
      limit: ratingFixErrorIds.value.length
    })
    mergeRatingFixItems(result.items || [])
    const remaining = (result.items || []).filter(i => i.plan === 'error').length
    if (remaining === 0) {
      ElMessage.success('全部失败项已重试成功')
    } else {
      ElMessage.warning(`重试完成，仍有 ${remaining} 行网络失败，可再次重试`)
    }
  } catch (error) {
    ElMessage.error(apiErrorDetail(error, '重试失败'))
  } finally {
    ratingFixRetrying.value = false
  }
}

const diagnosing = ref(false)
const diagnoseDialogVisible = ref(false)
const diagnoseResult = ref(null)

// ---- 计算属性 ----
const currentMode = computed(() => tables.value.find(t => t.name === activeTable.value)?.mode || 'readonly')
const displayColumns = computed(() => columns.value.slice(0, 30))
const editColumns = computed(() => columns.value.filter(c => !c.pk || editingRow.value == null))
const editLabel = computed(() => {
  const row = editingRow.value
  if (!row) return ''
  return row.id ?? JSON.stringify(row)
})
const scopeLabel = computed(() => (renameScopedToSelection.value ? `已选 ${renamePreview.value?.total || 0} 行` : '全库'))
const restoreCandidates = computed(() => [...backups.value, ...snapshots.value])
const snapshotCount = computed(() => snapshots.value.length)

// ---- 工具 ----
function renderCell(value) {
  if (value === null || value === undefined) return 'NULL'
  const text = typeof value === 'string' ? value : JSON.stringify(value)
  return text
}

function isLongValue(value) {
  return typeof value === 'string' && value.length > 80
}

function isBoolColumn(col) {
  return /BOOL|TINYINT\(1\)/i.test(col.type || '')
}

function isIntColumn(col) {
  return /INT|BIGINT/i.test(col.type || '') && !/BOOL/i.test(col.type || '')
}

function columnWidth(col) {
  const t = (col.type || '').toUpperCase()
  if (t.includes('TEXT') || t.includes('JSON')) return 260
  if (t.includes('DATETIME')) return 170
  if (/INT|FLOAT|BOOL/.test(t)) return 110
  return 180
}

function kindLabel(kind) {
  return { activate: '原始备份', auto: '自动备份', manual: '手动备份', snapshot: '回滚快照', 'pre-restore': '恢复前备份' }[kind] || kind
}

function kindTagType(kind) {
  return { activate: 'primary', auto: 'success', manual: '', snapshot: 'warning', 'pre-restore': 'info' }[kind] || 'info'
}

function formatSize(bytes) {
  if (!bytes && bytes !== 0) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function apiErrorDetail(error, fallback) {
  return error?.response?.data?.detail || error?.message || fallback
}

// ---- 数据加载 ----
async function loadConfig() {
  try {
    const config = await configApi.get()
    dbPath.value = config?.kikoeru_db?.db_path || ''
    featureEnabled.value = Boolean(config?.kikoeru_db?.enabled)
  } catch (error) {
    console.warn('[KikoeruDb] 配置读取失败', error)
  }
}

async function loadScanStatus() {
  try {
    scanStatus.value = await kikoeruDbApi.scanStatus()
  } catch {
    scanStatus.value = { listening: false, last_scan_finished_at: '' }
  }
}

async function loadTables() {
  try {
    const data = await kikoeruDbApi.tables()
    tables.value = data.tables || []
    if (!activeTable.value && tables.value.length > 0) {
      activeTable.value = tables.value[0].name
    }
  } catch (error) {
    ElMessage.error(apiErrorDetail(error, '读取表清单失败'))
  }
}

async function loadRows() {
  if (!activeTable.value) return
  loadingRows.value = true
  try {
    const data = await kikoeruDbApi.rows(activeTable.value, {
      page: page.value,
      size: pageSize.value,
      search: searchText.value,
      sort: ''
    })
    columns.value = data.columns || []
    rows.value = data.rows || []
    total.value = data.total || 0
  } catch (error) {
    ElMessage.error(apiErrorDetail(error, '读取数据失败'))
  } finally {
    loadingRows.value = false
  }
}

async function loadBackups() {
  try {
    const data = await kikoeruDbApi.backups()
    backups.value = (data.backups || []).filter(b => b.kind !== 'snapshot')
    const snapData = await kikoeruDbApi.snapshots()
    snapshots.value = snapData.snapshots || []
  } catch {
    /* 未启用时静默 */
  }
}

function onSelectionChange(selection) {
  selectedRows.value = selection
}

async function refreshAll() {
  await Promise.all([loadConfig(), loadScanStatus(), loadTables(), loadBackups()])
  await loadRows()
}

// ---- 编辑 / 新增 / 删除 ----
function openEditDialog(row) {
  editingRow.value = row
  editForm.value = {}
  for (const col of columns.value) {
    let value = row[col.name]
    if (value !== null && typeof value === 'object') value = JSON.stringify(value)
    editForm.value[col.name] = value
  }
  editDialogVisible.value = true
}

function openCreateDialog() {
  editingRow.value = null
  editForm.value = {}
  for (const col of columns.value) {
    if (col.pk && col.name === 'id') continue // 自增主键不填
    editForm.value[col.name] = isBoolColumn(col) ? false : null
  }
  editDialogVisible.value = true
}

function parseMaybeJson(col, value) {
  if (value === '' || value === null || value === undefined) return null
  if (/JSON/i.test(col.type || '')) {
    try {
      return JSON.parse(value)
    } catch {
      return value
    }
  }
  return value
}

async function saveRow() {
  savingRow.value = true
  try {
    const patch = {}
    for (const col of columns.value) {
      const raw = editForm.value[col.name]
      const value = parseMaybeJson(col, raw)
      if (editingRow.value) {
        const before = editingRow.value[col.name] ?? null
        if (JSON.stringify(value) === JSON.stringify(before)) continue
        patch[col.name] = value
      } else if (value !== null && value !== undefined) {
        patch[col.name] = value
      }
    }
    if (editingRow.value) {
      if (Object.keys(patch).length === 0) {
        ElMessage.info('没有修改任何字段')
        editDialogVisible.value = false
        return
      }
      const id = columns.value.filter(c => c.pk).length > 1
        ? Object.fromEntries(columns.value.filter(c => c.pk).map(c => [c.name, editingRow.value[c.name]]))
        : editingRow.value[columns.value.find(c => c.pk)?.name || 'rowid']
      const result = await kikoeruDbApi.updateRow(activeTable.value, id, patch)
      ElMessage.success(
        result.channel === 'api'
          ? '已通过 Kikoeru API 写入'
          : '已直接写入数据库（已自动生成回滚快照）'
      )
    } else {
      await kikoeruDbApi.insertRow(activeTable.value, patch)
      ElMessage.success('新增成功')
    }
    editDialogVisible.value = false
    await loadRows()
    await loadBackups()
  } catch (error) {
    ElMessage.error(apiErrorDetail(error, '保存失败'))
  } finally {
    savingRow.value = false
  }
}

async function deleteSelected() {
  if (selectedRows.value.length === 0) return
  try {
    await ElMessageBox.confirm(
      `确定删除选中的 ${selectedRows.value.length} 行？删除前会自动生成回滚快照。`,
      '删除确认',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
    )
  } catch {
    return
  }
  const pkCols = columns.value.filter(c => c.pk)
  let ok = 0
  for (const row of selectedRows.value) {
    try {
      const id = pkCols.length > 1
        ? Object.fromEntries(pkCols.map(c => [c.name, row[c.name]]))
        : row[pkCols[0]?.name || 'rowid']
      await kikoeruDbApi.deleteRow(activeTable.value, id)
      ok += 1
    } catch (error) {
      ElMessage.error(apiErrorDetail(error, `删除行失败: ${JSON.stringify(row).slice(0, 60)}`))
    }
  }
  if (ok > 0) {
    ElMessage.success(`已删除 ${ok} 行`)
    await loadRows()
    await loadBackups()
  }
}

// ---- 备份 / 恢复 ----
async function doBackup() {
  backingUp.value = true
  try {
    const result = await kikoeruDbApi.createBackup()
    ElMessage.success(`备份完成: ${result.filename}`)
    await loadBackups()
  } catch (error) {
    ElMessage.error(apiErrorDetail(error, '备份失败'))
  } finally {
    backingUp.value = false
  }
}

function confirmRestore(backup) {
  restoreSelection.value = backup
  restoreDialogVisible.value = true
}

async function doRestore() {
  if (!restoreSelection.value) return
  try {
    await ElMessageBox.confirm(
      `确定恢复到「${restoreSelection.value.filename}」？当前数据库会先自动备份（pre-restore）。`,
      '恢复确认',
      { type: 'warning', confirmButtonText: '确认恢复', cancelButtonText: '取消' }
    )
  } catch {
    return
  }
  restoring.value = true
  try {
    await kikoeruDbApi.restoreBackup(restoreSelection.value.filename)
    ElMessage.success('恢复完成')
    restoreDialogVisible.value = false
    await Promise.all([loadBackups(), loadRows()])
  } catch (error) {
    ElMessage.error(apiErrorDetail(error, '恢复失败'))
  } finally {
    restoring.value = false
  }
}

// ---- 一键套用命名 ----
async function openRenameWizard(scoped) {
  renameScopedToSelection.value = scoped
  renameWizardVisible.value = true
  await loadRenamePreview()
}

async function loadRenamePreview() {
  renamePreviewing.value = true
  try {
    const ids = renameScopedToSelection.value
      ? selectedRows.value.map(r => r.id).filter(v => v !== undefined)
      : null
    if (renameScopedToSelection.value && (!ids || ids.length === 0)) {
      ElMessage.warning('当前表不支持按行选择（无 id 列），改为全库预览')
      renameScopedToSelection.value = false
    }
    renamePreview.value = await kikoeruDbApi.renamePreview(
      renameScopedToSelection.value ? ids : null
    )
  } catch (error) {
    ElMessage.error(apiErrorDetail(error, '生成预览失败'))
  } finally {
    renamePreviewing.value = false
  }
}

async function doApplyRename() {
  try {
    await ElMessageBox.confirm(
      `确定对 ${scopeLabel.value} 中 ${renamePreview.value.changed} 行执行改名？执行前会自动生成回滚快照。`,
      '执行确认',
      { type: 'warning', confirmButtonText: '执行', cancelButtonText: '取消' }
    )
  } catch {
    return
  }
  renameApplying.value = true
  try {
    const ids = renameScopedToSelection.value
      ? (renamePreview.value.items || []).filter(i => i.changed).map(i => i.id)
      : null
    const result = await kikoeruDbApi.renameApply(ids)
    ElMessage.success(`已套用 ${result.applied} 行（跳过 ${result.skipped}）`)
    renameWizardVisible.value = false
    await Promise.all([loadRows(), loadBackups()])
  } catch (error) {
    ElMessage.error(apiErrorDetail(error, '执行失败'))
  } finally {
    renameApplying.value = false
  }
}

// ---- 评分修复 ----
async function openRatingFixWizard(scoped) {
  ratingFixScopedToSelection.value = scoped
  ratingFixVisible.value = true
  await loadRatingFixPreview()
}

async function loadRatingFixPreview() {
  ratingFixPreviewing.value = true
  try {
    let ids = null
    if (ratingFixScopedToSelection.value) {
      ids = selectedRows.value.map(r => r.id).filter(v => v !== undefined)
      if (!ids.length) {
        ElMessage.warning('当前选择不支持按行处理（无 id 列），改为全库 0 分作品')
        ratingFixScopedToSelection.value = false
      }
    }
    ratingFixPreview.value = await kikoeruDbApi.ratingFixPreview({
      ids: ratingFixScopedToSelection.value ? ids : null,
      limit: ratingFixLimit.value
    })
  } catch (error) {
    ElMessage.error(apiErrorDetail(error, '生成评分修复预览失败'))
  } finally {
    ratingFixPreviewing.value = false
  }
}

async function doApplyRatingFix() {
  try {
    await ElMessageBox.confirm(
      `确定回填 ${ratingFixPreview.value.fixable} 行评分？执行前会自动生成回滚快照。`,
      '执行确认',
      { type: 'warning', confirmButtonText: '执行', cancelButtonText: '取消' }
    )
  } catch {
    return
  }
  ratingFixApplying.value = true
  try {
    const result = await kikoeruDbApi.ratingFixApply({
      ids: ratingFixScopedToSelection.value && selectedRows.value.length
        ? selectedRows.value.map(r => r.id).filter(v => v !== undefined)
        : null,
      limit: ratingFixLimit.value
    })
    ElMessage.success(`已回填 ${result.applied} 行评分（无评分 ${result.skipped} 行保持不变）`)
    ratingFixVisible.value = false
    await Promise.all([loadRows(), loadBackups()])
  } catch (error) {
    ElMessage.error(apiErrorDetail(error, '评分修复执行失败'))
  } finally {
    ratingFixApplying.value = false
  }
}

// ---- 检测 ----
async function runDiagnose() {
  diagnosing.value = true
  try {
    diagnoseResult.value = await kikoeruDbApi.diagnose()
    diagnoseDialogVisible.value = true
  } catch (error) {
    ElMessage.error(apiErrorDetail(error, '检测失败'))
  } finally {
    diagnosing.value = false
  }
}

// ---- 表切换 ----
watch(activeTable, () => {
  page.value = 1
  searchText.value = ''
  selectedRows.value = []
  loadRows()
})

onMounted(refreshAll)
onActivated(() => {
  loadConfig()
  loadScanStatus()
})
</script>

<style scoped>
.kikoeru-db-page :deep(.el-table .cell) {
  font-variant-numeric: tabular-nums;
}
</style>

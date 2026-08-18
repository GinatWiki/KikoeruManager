<template>
  <div class="duplicate-check-page">
    <!-- 页头 -->
    <AppPageHeader
      :icon="GitCompare"
      icon-color="var(--km-nav-duplicate-icon, #8b5cf6)"
      title="仓库查重"
      subtitle="检测库存中同一项目的重复版本，比对后选择保留或删除"
    >
      <div class="duplicate-toolbar">
        <div class="duplicate-search-wrap">
          <Search :size="13" class="duplicate-search-icon" />
          <el-input
            v-model="searchQuery"
            placeholder="搜索 RJ 号..."
            clearable
            size="small"
            @keyup.enter="fetchGroups"
            @clear="fetchGroups"
          />
        </div>
        <AppDropdown
          v-model="sortKey"
          :options="sortOptions"
          placeholder="排序"
          size="small"
          @update:model-value="fetchGroups"
        />
        <StatefulButton
          class="duplicate-refresh-btn"
          type="button"
          unstyled
          :show-default-icons="false"
          :success-hold="900"
          :disabled="loading"
          @click="fetchGroups"
        >
          <template #prefix="{ state }">
            <span class="duplicate-refresh-icon-wrap" :class="`is-${state}`" aria-hidden="true">
              <Loader2 v-if="state === 'loading' || loading" :size="14" :stroke-width="2.5" class="animate-spin" />
              <CheckCircle2 v-else-if="state === 'success'" :size="14" :stroke-width="2.5" />
              <RefreshCw v-else :size="14" :stroke-width="2.5" />
            </span>
          </template>
          刷新
        </StatefulButton>
      </div>
    </AppPageHeader>

    <!-- 错误提示 -->
    <div v-if="errorMessage" class="duplicate-error-alert">
      <AlertCircle class="w-5 h-5 flex-shrink-0 mt-0.5 text-red-500" />
      <div>
        <h3 class="font-medium">获取重复列表失败</h3>
        <p class="text-sm mt-1 opacity-90">{{ errorMessage }}</p>
      </div>
    </div>

    <!-- 主内容区 -->
    <div class="duplicate-main" :class="{ 'has-detail': activeRjcode }">
      <!-- 卡片网格区域 -->
      <div class="duplicate-grid-area">
        <!-- 加载态 -->
        <div v-if="loading && groups.length === 0" class="duplicate-loading">
          <AppLoadingAnimation variant="inline" :size="28" />
          <span>正在扫描重复版本...</span>
        </div>

        <!-- 空态 -->
        <div v-else-if="groups.length === 0 && !loading" class="duplicate-empty">
          <AppEmptyState description="没有发现重复版本，库存中的项目都是唯一的" size="lg" />
        </div>

        <!-- 卡片网格 -->
        <div v-else class="duplicate-card-grid" :class="{ 'is-dimmed': activeRjcode }">
          <div
            v-for="group in groups"
            :key="group.rjcode"
            class="duplicate-card-cell"
          >
            <WorkCard
              :item="buildWorkCardItem(group)"
              :card-index="0"
              :image-active="true"
              :image-field="'image_url'"
              :code-field="'rjcode'"
              :show-release-badge="false"
              :size="'lg'"
              @select="selectGroup(group)"
            >
              <template #meta>
                <div class="duplicate-card-meta">
                  <span class="duplicate-card-meta-item is-versions">
                    <Layers :size="11" />
                    {{ group.version_count }} 个版本
                  </span>
                  <span class="duplicate-card-meta-item is-libraries">
                    <File :size="11" />
                    {{ group.file_count }} 个文件
                  </span>
                </div>
              </template>
              <template #tags>
                <div class="duplicate-card-tags">
                  <span class="tag-chip is-duplicate">
                    <GitCompare :size="11" />
                    重复
                  </span>
                  <span class="tag-chip is-size">
                    {{ formatSize(group.total_size) }}
                  </span>
                </div>
              </template>
              <template #actions>
                <div class="duplicate-card-actions">
                  <button class="work-action-btn" @click.stop="selectGroup(group)">
                    比对版本
                  </button>
                </div>
              </template>
            </WorkCard>
          </div>
        </div>

        <!-- 分页 -->
        <div v-if="total > pageSize" class="duplicate-pager">
          <el-pagination
            small
            background
            layout="prev, pager, next"
            :total="total"
            :page-size="pageSize"
            :current-page="currentPage"
            @current-change="handlePageChange"
          />
        </div>
      </div>

      <!-- 版本比对详情面板 -->
      <transition name="duplicate-detail-slide">
        <div v-if="activeRjcode" class="duplicate-detail-panel" :class="{ 'is-wide': detailTab === 'diff' }">
          <!-- 详情加载态 -->
          <div v-if="detailLoading" class="duplicate-detail-loading">
            <AppLoadingAnimation variant="inline" :size="24" />
            <span>加载版本详情...</span>
          </div>

          <template v-else>
            <!-- 详情头部 -->
            <div class="duplicate-detail-head">
              <div class="duplicate-detail-head-left">
                <button class="duplicate-detail-back" @click="closeDetail" title="返回列表">
                  <ArrowLeft :size="18" :stroke-width="2.5" />
                </button>
                <div>
                  <h3 class="duplicate-detail-title">{{ activeRjcode }}</h3>
                  <span class="duplicate-detail-subtitle">
                    {{ versions.length }} 个版本 · {{ detailEntries.length }} 个文件
                  </span>
                </div>
              </div>
              <StatefulButton
                class="duplicate-keep-btn"
                type="button"
                unstyled
                :show-default-icons="false"
                :disabled="!selectedVersionKeys.size || batchRunning"
                :loading="batchRunning"
                @click="handleKeepSelected"
              >
                <template #prefix>
                  <Check :size="14" :stroke-width="2.5" />
                </template>
                保留选中 ({{ selectedVersionKeys.size }})
              </StatefulButton>
            </div>

            <!-- 详情页签 -->
            <div class="duplicate-detail-tabs">
              <button
                class="duplicate-detail-tab"
                :class="{ 'is-active': detailTab === 'versions' }"
                @click="detailTab = 'versions'"
              >
                版本列表
              </button>
              <button
                class="duplicate-detail-tab"
                :class="{ 'is-active': detailTab === 'diff' }"
                :disabled="versions.length < 2"
                @click="detailTab = 'diff'"
              >
                差异比对
              </button>
            </div>

            <!-- 版本列表 -->
            <div v-if="detailTab === 'versions'" class="duplicate-detail-body">
              <div
                v-for="version in versions"
                :key="version.version_key"
                class="duplicate-version-card"
                :class="{ 'is-selected': selectedVersionKeys.has(version.version_key) }"
                @click="toggleVersionSelection(version.version_key)"
              >
                <div class="duplicate-version-card-check">
                  <div class="duplicate-version-radio" :class="{ 'is-checked': selectedVersionKeys.has(version.version_key) }">
                    <Check v-if="selectedVersionKeys.has(version.version_key)" :size="12" :stroke-width="3" />
                  </div>
                </div>
                <div class="duplicate-version-card-body">
                  <div class="duplicate-version-card-name">
                    <span class="duplicate-version-card-name-text" :title="version.root_name || version.library_name">
                      {{ version.root_name || version.library_name }}
                    </span>
                    <span
                      v-if="version.language"
                      class="duplicate-version-lang"
                      :class="langChipClass(version.language)"
                    >
                      {{ version.language }}
                    </span>
                  </div>
                  <div class="duplicate-version-card-path">
                    <FolderTree :size="12" />
                    <span class="duplicate-version-card-path-text" :title="version.root_path">
                      {{ version.root_path || version.library_name }}
                    </span>
                  </div>
                  <div class="duplicate-version-card-meta">
                    <span class="duplicate-version-meta-item">
                      <HardDrive :size="11" />
                      {{ version.library_name }}
                    </span>
                    <span class="duplicate-version-meta-item">
                      <Folder :size="11" />
                      {{ formatSize(version.total_size) }}
                    </span>
                    <span class="duplicate-version-meta-item">
                      <File :size="11" />
                      {{ version.file_count }} 个文件
                    </span>
                    <span v-if="version.max_mtime" class="duplicate-version-meta-item">
                      <Clock :size="11" />
                      {{ formatDate(version.max_mtime) }}
                    </span>
                  </div>
                  <button
                    class="duplicate-version-files-toggle"
                    @click.stop="toggleVersionFiles(version.version_key)"
                  >
                    <ChevronDown
                      :size="12"
                      class="duplicate-version-files-chevron"
                      :class="{ 'is-open': expandedVersionKeys.has(version.version_key) }"
                    />
                    {{ expandedVersionKeys.has(version.version_key) ? '收起文件列表' : `查看内部文件（${version.file_count}）` }}
                  </button>
                  <div
                    v-if="expandedVersionKeys.has(version.version_key)"
                    class="duplicate-version-files"
                    @click.stop
                  >
                    <div v-if="!version.files || version.files.length === 0" class="duplicate-version-files-empty">
                      索引中暂无内部文件记录
                    </div>
                    <template v-else>
                      <div class="duplicate-version-files-list">
                        <div
                          v-for="item in version.files"
                          :key="item.entry_type + ':' + item.path"
                          class="duplicate-version-file-row"
                          :style="{ paddingLeft: `${8 + fileDepth(item) * 14}px` }"
                          :title="item.path"
                        >
                          <Folder v-if="item.entry_type === 'dir'" :size="11" class="is-dir" />
                          <File v-else :size="11" />
                          <span class="duplicate-version-file-name">{{ fileBaseName(item) }}</span>
                          <span class="duplicate-version-file-size">{{ formatSize(item.size) }}</span>
                        </div>
                      </div>
                      <div v-if="version.files_truncated" class="duplicate-version-files-truncated">
                        文件过多，仅显示前 {{ version.files.length }} 条，差异比对结果可能不完整
                      </div>
                    </template>
                  </div>
                </div>
              </div>
            </div>

            <!-- 差异比对 -->
            <div v-else class="duplicate-detail-body duplicate-diff">
              <div class="duplicate-diff-selectors">
                <AppDropdown
                  v-model="compareA"
                  :options="versionOptions"
                  placeholder="版本 A"
                  size="small"
                  class="duplicate-diff-selector"
                />
                <GitCompare :size="13" class="duplicate-diff-vs" />
                <AppDropdown
                  v-model="compareB"
                  :options="versionOptions"
                  placeholder="版本 B"
                  size="small"
                  class="duplicate-diff-selector"
                />
              </div>

              <div v-if="compareTruncated" class="duplicate-diff-warning">
                <AlertTriangle :size="12" />
                有版本的文件列表被截断，比对结果可能不完整
              </div>

              <template v-if="diffResult">
                <div class="duplicate-diff-summary">
                  <button
                    v-for="chip in diffFilterChips"
                    :key="chip.value"
                    class="duplicate-diff-chip"
                    :class="[{ 'is-active': diffFilter === chip.value }, chip.tone]"
                    @click="diffFilter = chip.value"
                  >
                    {{ chip.label }}
                    <span v-if="chip.count != null" class="duplicate-diff-chip-count">{{ chip.count }}</span>
                  </button>
                </div>
                <div v-if="filteredDiffRows.length === 0" class="duplicate-diff-empty">
                  当前筛选下没有文件差异
                </div>
                <div v-else class="duplicate-diff-list">
                  <div
                    v-for="row in filteredDiffRows"
                    :key="row.status + ':' + row.path"
                    class="duplicate-diff-row"
                    :title="row.path"
                  >
                    <span class="duplicate-diff-status" :class="`is-${row.status}`">
                      {{ diffStatusLabel(row.status) }}
                    </span>
                    <span class="duplicate-diff-path">{{ row.path }}</span>
                    <span class="duplicate-diff-size">{{ diffSizeText(row) }}</span>
                  </div>
                </div>
              </template>
              <div v-else class="duplicate-diff-empty">
                请选择两个不同版本进行比对
              </div>
            </div>
          </template>
        </div>
      </transition>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, ref } from 'vue'
import {
  AlertCircle,
  AlertTriangle,
  ArrowLeft,
  Check,
  CheckCircle2,
  ChevronDown,
  Clock,
  File,
  Folder,
  FolderTree,
  GitCompare,
  HardDrive,
  Layers,
  Loader2,
  RefreshCw,
  Search,
} from 'lucide-vue-next'
import { duplicateCheckApi } from '../api'
import { showSystemConfirm } from '../composables/useSystemPrompt'
import AppDropdown from '../components/common/AppDropdown.vue'
import AppEmptyState from '../components/common/AppEmptyState.vue'
import AppLoadingAnimation from '../components/common/AppLoadingAnimation.vue'
import AppPageHeader from '../components/common/AppPageHeader.vue'
import WorkCard from '../components/circle/WorkCard.vue'
import StatefulButton from '../components/ui/stateful-button.vue'

// ── 状态 ──
const loading = ref(false)
const errorMessage = ref('')
const groups = ref([])
const total = ref(0)
const currentPage = ref(1)
const pageSize = ref(20)
const searchQuery = ref('')
const sortKey = ref('version_count')

const sortOptions = [
  { label: '版本数 ↓', value: 'version_count' },
  { label: '总大小 ↓', value: 'total_size' },
  { label: 'RJ 号 ↑', value: 'rjcode' },
  { label: '最近修改 ↓', value: 'max_mtime' },
]

// ── 详情面板 ──
const activeRjcode = ref('')
const detailLoading = ref(false)
const detailEntries = ref([])
const versions = ref([])
const selectedVersionKeys = ref(new Set())
const batchRunning = ref(false)

// ── 页签 / 文件展开 / 差异比对 ──
const detailTab = ref('versions')
const expandedVersionKeys = ref(new Set())
const compareA = ref('')
const compareB = ref('')
const diffFilter = ref('changes')

const diffFilterOptions = [
  { label: '只看差异', value: 'changes', tone: 'is-changes' },
  { label: '仅 A 有', value: 'onlyA', tone: 'is-onlyA', countKey: 'onlyA' },
  { label: '仅 B 有', value: 'onlyB', tone: 'is-onlyB', countKey: 'onlyB' },
  { label: '大小不同', value: 'diffSize', tone: 'is-diffSize', countKey: 'diffSize' },
  { label: '完全相同', value: 'same', tone: 'is-same', countKey: 'same' },
  { label: '全部', value: 'all', tone: 'is-all' },
]

// ── 构建 WorkCard 数据 ──
function buildWorkCardItem(group) {
  return {
    rjcode: group.rjcode,
    canonical_rjcode: group.rjcode,
    title: group.project_name || group.rjcode,
    image_url: `/api/circle-completion/cover/${group.rjcode}.jpg`,
    owned: true,
    has_asmr_one: false,
    cvs: [],
    release_date: '',
    _group: group,
  }
}

// ── 获取分组列表 ──
async function fetchGroups() {
  loading.value = true
  errorMessage.value = ''
  try {
    const data = await duplicateCheckApi.groups({
      page: currentPage.value,
      pageSize: pageSize.value,
      sort: sortKey.value,
      search: searchQuery.value.trim(),
    })
    groups.value = data.groups || []
    total.value = data.total || 0
  } catch (err) {
    errorMessage.value = err?.response?.data?.detail || err.message || '获取重复列表失败'
  } finally {
    loading.value = false
  }
}

// ── 选择分组 → 打开详情面板 ──
async function selectGroup(group) {
  if (activeRjcode.value === group.rjcode) return
  activeRjcode.value = group.rjcode
  selectedVersionKeys.value = new Set()
  detailLoading.value = true
  try {
    const data = await duplicateCheckApi.groupDetail(group.rjcode)
    detailEntries.value = data.entries || []
    versions.value = data.versions || []
    // 默认选中第一个版本
    if (versions.value.length > 0) {
      selectedVersionKeys.value = new Set([versions.value[0].version_key])
    }
    // 重置页签 / 展开 / 比对状态
    detailTab.value = 'versions'
    expandedVersionKeys.value = new Set()
    diffFilter.value = 'changes'
    compareA.value = versions.value[0]?.version_key || ''
    compareB.value = versions.value[1]?.version_key || versions.value[0]?.version_key || ''
    await nextTick()
  } catch (err) {
    errorMessage.value = err?.response?.data?.detail || err.message || '获取版本详情失败'
  } finally {
    detailLoading.value = false
  }
}

function closeDetail() {
  activeRjcode.value = ''
  detailEntries.value = []
  versions.value = []
  selectedVersionKeys.value = new Set()
  detailTab.value = 'versions'
  expandedVersionKeys.value = new Set()
  compareA.value = ''
  compareB.value = ''
  diffFilter.value = 'changes'
}

// ── 版本语言标签 ──
function langChipClass(language) {
  const text = String(language || '')
  if (text.includes('简体')) return 'is-sc'
  if (text.includes('繁体')) return 'is-tc'
  if (text.includes('翻译')) return 'is-tr'
  if (text.includes('日文')) return 'is-jp'
  if (text.includes('英文')) return 'is-en'
  return 'is-unknown'
}

// ── 版本内文件展开 ──
function toggleVersionFiles(versionKey) {
  const next = new Set(expandedVersionKeys.value)
  if (next.has(versionKey)) {
    next.delete(versionKey)
  } else {
    next.add(versionKey)
  }
  expandedVersionKeys.value = next
}

function fileDepth(item) {
  return Math.max(String(item?.path || '').split('/').length - 1, 0)
}

function fileBaseName(item) {
  const path = String(item?.path || '')
  return path.split('/').pop() || path
}

// ── 差异比对 ──
const versionOptions = computed(() =>
  versions.value.map((v, index) => ({
    label: `版本${index + 1} · ${v.root_name || v.library_name}${v.language ? `（${v.language}）` : ''}`,
    value: v.version_key,
  }))
)

const compareTruncated = computed(() => {
  const a = versions.value.find(v => v.version_key === compareA.value)
  const b = versions.value.find(v => v.version_key === compareB.value)
  return Boolean(a?.files_truncated || b?.files_truncated)
})

const diffResult = computed(() => {
  const a = versions.value.find(v => v.version_key === compareA.value)
  const b = versions.value.find(v => v.version_key === compareB.value)
  if (!a || !b || a.version_key === b.version_key) return null

  const mapA = new Map()
  for (const item of a.files || []) {
    if (item.entry_type === 'file') mapA.set(item.path, item.size || 0)
  }
  const mapB = new Map()
  for (const item of b.files || []) {
    if (item.entry_type === 'file') mapB.set(item.path, item.size || 0)
  }

  const rows = []
  const counts = { onlyA: 0, onlyB: 0, diffSize: 0, same: 0 }
  for (const [path, size] of mapA) {
    if (mapB.has(path)) {
      const sizeB = mapB.get(path)
      if (sizeB === size) {
        rows.push({ path, status: 'same', sizeA: size, sizeB })
        counts.same += 1
      } else {
        rows.push({ path, status: 'diffSize', sizeA: size, sizeB })
        counts.diffSize += 1
      }
    } else {
      rows.push({ path, status: 'onlyA', sizeA: size })
      counts.onlyA += 1
    }
  }
  for (const [path, size] of mapB) {
    if (!mapA.has(path)) {
      rows.push({ path, status: 'onlyB', sizeB: size })
      counts.onlyB += 1
    }
  }
  rows.sort((x, y) => String(x.path).localeCompare(String(y.path)))
  return { rows, counts }
})

const diffFilterChips = computed(() => {
  const counts = diffResult.value?.counts || {}
  return diffFilterOptions.map(option => ({
    ...option,
    count: option.countKey != null ? counts[option.countKey] ?? 0 : null,
  }))
})

const filteredDiffRows = computed(() => {
  const result = diffResult.value
  if (!result) return []
  if (diffFilter.value === 'all') return result.rows
  if (diffFilter.value === 'changes') return result.rows.filter(row => row.status !== 'same')
  return result.rows.filter(row => row.status === diffFilter.value)
})

function diffStatusLabel(status) {
  return {
    onlyA: '仅 A',
    onlyB: '仅 B',
    diffSize: '大小不同',
    same: '相同',
  }[status] || status
}

function diffSizeText(row) {
  if (row.status === 'onlyA') return formatSize(row.sizeA)
  if (row.status === 'onlyB') return formatSize(row.sizeB)
  if (row.status === 'diffSize') return `${formatSize(row.sizeA)} → ${formatSize(row.sizeB)}`
  return formatSize(row.sizeA)
}

// ── 版本选择 ──
function toggleVersionSelection(versionKey) {
  const next = new Set(selectedVersionKeys.value)
  if (next.has(versionKey)) {
    if (next.size <= 1) return // 至少保留一个
    next.delete(versionKey)
  } else {
    next.add(versionKey)
  }
  selectedVersionKeys.value = next
}

// ── 执行保留操作 ──
async function handleKeepSelected() {
  if (!selectedVersionKeys.value.size) return
  const rjcode = activeRjcode.value
  const deleteCount = versions.value.length - selectedVersionKeys.value.size

  if (deleteCount <= 0) {
    showSystemConfirm({
      title: '无需操作',
      message: '已选中所有版本，没有需要删除的版本。',
      confirmText: '知道了',
      showCancel: false,
    })
    return
  }

  const confirmed = await showSystemConfirm({
    title: '确认删除重复版本',
    message: `将保留 ${selectedVersionKeys.value.size} 个选中版本，删除其余 ${deleteCount} 个版本。此操作不可撤销，确定继续？`,
    confirmText: '确认删除',
    confirmType: 'danger',
  })

  if (!confirmed) return

  // 收集保留版本中的所有条目 ID
  const keepEntryIds = []
  for (const version of versions.value) {
    if (selectedVersionKeys.value.has(version.version_key)) {
      const versionEntries = detailEntries.value.filter(
        e => e.version_key === version.version_key
      )
      for (const entry of versionEntries) {
        keepEntryIds.push(entry.id)
      }
    }
  }

  batchRunning.value = true
  try {
    const result = await duplicateCheckApi.keep(rjcode, keepEntryIds)
    const deletedCount = result.deleted_count || 0
    const failedCount = result.failed_count || 0

    if (failedCount > 0) {
      showSystemConfirm({
        title: '部分删除失败',
        message: `成功删除 ${deletedCount} 个版本，${failedCount} 个版本删除失败。请检查日志了解详情。`,
        confirmText: '知道了',
        showCancel: false,
      })
    }

    closeDetail()
    await fetchGroups()
  } catch (err) {
    errorMessage.value = err?.response?.data?.detail || err.message || '删除操作失败'
  } finally {
    batchRunning.value = false
  }
}

function handlePageChange(page) {
  currentPage.value = page
  closeDetail()
  fetchGroups()
}

// ── 工具函数 ──
function formatSize(bytes) {
  if (bytes == null || bytes < 0) return '-'
  if (bytes === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const idx = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
  return `${(bytes / Math.pow(1024, idx)).toFixed(idx > 0 ? 1 : 0)} ${units[idx]}`
}

function formatDate(ts) {
  if (!ts) return '-'
  const d = new Date(ts)
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

onMounted(() => {
  fetchGroups()
})
</script>

<style scoped>
/* ── 页面容器 ── */
.duplicate-check-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  padding: 0 16px 16px;
  overflow: hidden;
}

/* ── 工具栏 ── */
.duplicate-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
}

.duplicate-search-wrap {
  position: relative;
  width: 180px;
}

.duplicate-search-icon {
  position: absolute;
  left: 10px;
  top: 50%;
  transform: translateY(-50%);
  color: #94a3b8;
  z-index: 1;
}

.duplicate-search-wrap :deep(.el-input__wrapper) {
  padding-left: 30px;
}

.duplicate-refresh-btn {
  font-size: 13px;
}

.duplicate-refresh-icon-wrap {
  display: flex;
  align-items: center;
  justify-content: center;
}

.duplicate-refresh-icon-wrap.is-loading {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

/* ── 错误提示 ── */
.duplicate-error-alert {
  display: flex;
  gap: 12px;
  padding: 12px 16px;
  margin: 0 0 12px;
  border-radius: 10px;
  background: rgba(239, 68, 68, 0.08);
  border: 1px solid rgba(239, 68, 68, 0.2);
  color: #dc2626;
  font-size: 13px;
}

/* ── 主内容区 ── */
.duplicate-main {
  flex: 1;
  display: flex;
  min-height: 0;
  overflow: hidden;
  gap: 0;
}

/* ── 卡片网格区 ── */
.duplicate-grid-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  overflow: hidden;
}

.duplicate-loading,
.duplicate-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  color: #94a3b8;
  font-size: 14px;
}

.duplicate-card-grid {
  flex: 1;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
  gap: 10px;
  overflow-y: auto;
  padding: 4px 2px;
  align-content: start;
  transition: opacity 0.2s;
}

.duplicate-card-grid.is-dimmed {
  opacity: 0.6;
}

/* ── 卡片内自定义内容 ── */
.duplicate-card-meta {
  display: flex;
  align-items: center;
  flex-wrap: nowrap;
  gap: 8px;
  font-size: 9px;
  color: var(--circle-text-muted, rgba(29, 29, 31, 0.40));
  line-height: 16px;
  height: 16px;
  overflow: hidden;
  white-space: nowrap;
}

.duplicate-card-meta-item {
  display: flex;
  align-items: center;
  gap: 3px;
}

.duplicate-card-meta-item.is-versions {
  color: #8b5cf6;
  font-weight: 600;
}

.duplicate-card-tags {
  display: flex;
  gap: 4px;
  align-items: center;
  flex-wrap: nowrap;
  height: 24px;
  padding-top: 3px;
}

.duplicate-card-tags .tag-chip {
  height: 19px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 3px;
  padding: 0 7px;
  border-radius: 5px;
  font-size: 9px;
  font-weight: 750;
  line-height: 1;
  letter-spacing: 0.02em;
  background: var(--circle-chip-bg, rgba(248, 250, 252, 0.72));
  border: 1px solid var(--circle-chip-border, rgba(203, 213, 225, 0.72));
  color: var(--circle-text-muted, #64748b);
  transition: transform .18s cubic-bezier(.34,1.56,.64,1);
}

.duplicate-card-tags .tag-chip.is-duplicate {
  background: rgba(139, 92, 246, 0.1);
  color: #8b5cf6;
  border-color: rgba(139, 92, 246, 0.25);
}

.duplicate-card-tags .tag-chip.is-size {
  background: rgba(100, 116, 139, 0.08);
  color: #64748b;
}

.duplicate-card-actions {
  display: flex;
  justify-content: stretch;
  gap: 4px;
  width: 100%;
  height: 28px;
  padding-top: 2px;
}

.duplicate-card-actions .work-action-btn {
  flex: 1;
  box-sizing: border-box;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid rgba(139, 92, 246, 0.3);
  background: rgba(139, 92, 246, 0.06);
  color: #8b5cf6;
  height: 24px;
  padding: 0 6px;
  border-radius: 8px;
  font-size: 9px;
  font-weight: 800;
  line-height: 1;
  cursor: pointer;
  transition: transform .22s cubic-bezier(.34,1.56,.64,1), background-color .18s ease, border-color .18s ease;
}

.duplicate-card-actions .work-action-btn:hover {
  background: rgba(139, 92, 246, 0.12);
  border-color: rgba(139, 92, 246, 0.5);
  transform: translateY(-2px);
}

/* ── 分页 ── */
.duplicate-pager {
  display: flex;
  justify-content: center;
  padding: 12px 0 4px;
  flex-shrink: 0;
}

/* ── 详情面板 ── */
.duplicate-detail-panel {
  width: 420px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  border-left: 1px solid #e2e8f0;
  background: #fff;
  margin-left: 12px;
  border-radius: 12px;
  border: 1px solid #e2e8f0;
  overflow: hidden;
}

/* 详情面板滑入动画 */
.duplicate-detail-slide-enter-active {
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

.duplicate-detail-slide-leave-active {
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
}

.duplicate-detail-slide-enter-from {
  width: 0;
  opacity: 0;
  margin-left: 0;
  border-width: 0;
}

.duplicate-detail-slide-leave-to {
  width: 0;
  opacity: 0;
  margin-left: 0;
  border-width: 0;
}

.duplicate-detail-loading {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: #94a3b8;
}

.duplicate-detail-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  padding: 14px 16px;
  border-bottom: 1px solid #f1f5f9;
  gap: 12px;
}

.duplicate-detail-head-left {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  min-width: 0;
}

.duplicate-detail-back {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: 8px;
  border: 1px solid #e2e8f0;
  background: #fff;
  color: #64748b;
  cursor: pointer;
  flex-shrink: 0;
  transition: all 0.2s;
}

.duplicate-detail-back:hover {
  background: #f1f5f9;
  color: #1e293b;
  border-color: #cbd5e1;
}

.duplicate-detail-title {
  font-size: 16px;
  font-weight: 700;
  color: #1e293b;
  margin: 0;
  font-family: 'SF Mono', 'Cascadia Code', monospace;
  line-height: 1.2;
}

.duplicate-detail-subtitle {
  font-size: 12px;
  color: #64748b;
  display: block;
  margin-top: 2px;
}

.duplicate-keep-btn {
  font-size: 12px;
  padding: 6px 14px;
  border-radius: 8px;
  background: #8b5cf6;
  color: #fff;
  border: none;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 6px;
  transition: all 0.2s;
  white-space: nowrap;
  flex-shrink: 0;
}

.duplicate-keep-btn:hover:not(:disabled) {
  background: #7c3aed;
  transform: translateY(-1px);
}

.duplicate-keep-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.duplicate-detail-body {
  flex: 1;
  overflow-y: auto;
  padding: 12px;
}

/* ── 版本卡片 ── */
.duplicate-version-card {
  display: flex;
  gap: 10px;
  padding: 12px;
  border-radius: 8px;
  border: 1px solid #e2e8f0;
  margin-bottom: 8px;
  cursor: pointer;
  transition: all 0.15s;
}

.duplicate-version-card:hover {
  border-color: #c4b5fd;
  background: #faf9ff;
}

.duplicate-version-card.is-selected {
  border-color: #8b5cf6;
  background: #f5f3ff;
}

.duplicate-version-card-check {
  display: flex;
  align-items: flex-start;
  padding-top: 1px;
}

.duplicate-version-radio {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  border: 2px solid #cbd5e1;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s;
}

.duplicate-version-radio.is-checked {
  border-color: #8b5cf6;
  background: #8b5cf6;
  color: #fff;
}

.duplicate-version-card-body {
  flex: 1;
  min-width: 0;
}

.duplicate-version-card-name {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  font-size: 13px;
  color: #1e293b;
  margin-bottom: 4px;
}

.duplicate-version-card-name-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ── 版本语言标签 ── */
.duplicate-version-lang {
  flex-shrink: 0;
  font-size: 9px;
  font-weight: 700;
  padding: 1px 6px;
  border-radius: 5px;
  line-height: 1.6;
  border: 1px solid transparent;
  white-space: nowrap;
}

.duplicate-version-lang.is-sc {
  background: rgba(16, 185, 129, 0.1);
  color: #059669;
  border-color: rgba(16, 185, 129, 0.25);
}

.duplicate-version-lang.is-tc {
  background: rgba(14, 165, 233, 0.1);
  color: #0284c7;
  border-color: rgba(14, 165, 233, 0.25);
}

.duplicate-version-lang.is-tr {
  background: rgba(245, 158, 11, 0.1);
  color: #d97706;
  border-color: rgba(245, 158, 11, 0.28);
}

.duplicate-version-lang.is-jp {
  background: rgba(244, 63, 94, 0.1);
  color: #e11d48;
  border-color: rgba(244, 63, 94, 0.25);
}

.duplicate-version-lang.is-en {
  background: rgba(99, 102, 241, 0.1);
  color: #4f46e5;
  border-color: rgba(99, 102, 241, 0.25);
}

.duplicate-version-lang.is-unknown {
  background: rgba(100, 116, 139, 0.08);
  color: #94a3b8;
  border-color: rgba(100, 116, 139, 0.2);
}

/* ── 版本内文件展开 ── */
.duplicate-version-files-toggle {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-top: 8px;
  padding: 2px 0;
  font-size: 11px;
  color: #8b5cf6;
  background: none;
  border: none;
  cursor: pointer;
  transition: color 0.15s;
}

.duplicate-version-files-toggle:hover {
  color: #7c3aed;
}

.duplicate-version-files-chevron {
  transition: transform 0.2s;
}

.duplicate-version-files-chevron.is-open {
  transform: rotate(180deg);
}

.duplicate-version-files {
  margin-top: 6px;
  border: 1px solid #f1f5f9;
  border-radius: 8px;
  background: #f8fafc;
  overflow: hidden;
}

.duplicate-version-files-empty {
  padding: 10px 12px;
  font-size: 11px;
  color: #94a3b8;
}

.duplicate-version-files-list {
  max-height: 240px;
  overflow-y: auto;
  padding: 6px 0;
}

.duplicate-version-file-row {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 2px 8px;
  font-size: 11px;
  color: #475569;
}

.duplicate-version-file-row svg {
  color: #94a3b8;
  flex-shrink: 0;
}

.duplicate-version-file-row svg.is-dir {
  color: #c4b5fd;
}

.duplicate-version-file-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.duplicate-version-file-size {
  flex-shrink: 0;
  font-size: 10px;
  color: #94a3b8;
}

.duplicate-version-files-truncated {
  padding: 5px 10px;
  font-size: 10px;
  color: #d97706;
  background: rgba(245, 158, 11, 0.06);
  border-top: 1px solid rgba(245, 158, 11, 0.12);
}

/* ── 详情页签 ── */
.duplicate-detail-tabs {
  display: flex;
  gap: 4px;
  padding: 0 16px;
  border-bottom: 1px solid #f1f5f9;
}

.duplicate-detail-tab {
  font-size: 12px;
  padding: 8px 12px;
  border: none;
  background: none;
  color: #64748b;
  cursor: pointer;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
  transition: color 0.15s, border-color 0.15s;
}

.duplicate-detail-tab:hover:not(:disabled) {
  color: #8b5cf6;
}

.duplicate-detail-tab.is-active {
  color: #8b5cf6;
  border-bottom-color: #8b5cf6;
  font-weight: 600;
}

.duplicate-detail-tab:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

/* ── 差异比对 ── */
.duplicate-detail-panel.is-wide {
  width: 560px;
}

.duplicate-diff-selectors {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 10px;
}

.duplicate-diff-selector {
  flex: 1;
  min-width: 0;
}

.duplicate-diff-vs {
  color: #94a3b8;
  flex-shrink: 0;
}

.duplicate-diff-warning {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  color: #d97706;
  background: rgba(245, 158, 11, 0.08);
  border: 1px solid rgba(245, 158, 11, 0.2);
  border-radius: 8px;
  padding: 6px 10px;
  margin-bottom: 10px;
}

.duplicate-diff-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 10px;
}

.duplicate-diff-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  padding: 3px 9px;
  border-radius: 999px;
  border: 1px solid #e2e8f0;
  background: #fff;
  color: #64748b;
  cursor: pointer;
  transition: all 0.15s;
}

.duplicate-diff-chip:hover {
  border-color: #c4b5fd;
  color: #8b5cf6;
}

.duplicate-diff-chip.is-active {
  background: rgba(139, 92, 246, 0.1);
  border-color: rgba(139, 92, 246, 0.4);
  color: #7c3aed;
  font-weight: 600;
}

.duplicate-diff-chip-count {
  font-size: 10px;
  font-weight: 700;
  opacity: 0.85;
}

.duplicate-diff-empty {
  padding: 32px 0;
  text-align: center;
  font-size: 12px;
  color: #94a3b8;
}

.duplicate-diff-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.duplicate-diff-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 6px;
  border-radius: 6px;
  font-size: 11px;
}

.duplicate-diff-row:hover {
  background: #f8fafc;
}

.duplicate-diff-status {
  flex-shrink: 0;
  font-size: 9px;
  font-weight: 700;
  padding: 1px 6px;
  border-radius: 5px;
  border: 1px solid transparent;
  white-space: nowrap;
}

.duplicate-diff-status.is-onlyA {
  background: rgba(139, 92, 246, 0.1);
  color: #7c3aed;
  border-color: rgba(139, 92, 246, 0.25);
}

.duplicate-diff-status.is-onlyB {
  background: rgba(14, 165, 233, 0.1);
  color: #0284c7;
  border-color: rgba(14, 165, 233, 0.25);
}

.duplicate-diff-status.is-diffSize {
  background: rgba(245, 158, 11, 0.1);
  color: #d97706;
  border-color: rgba(245, 158, 11, 0.28);
}

.duplicate-diff-status.is-same {
  background: rgba(16, 185, 129, 0.08);
  color: #059669;
  border-color: rgba(16, 185, 129, 0.22);
}

.duplicate-diff-path {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #334155;
}

.duplicate-diff-size {
  flex-shrink: 0;
  font-size: 10px;
  color: #94a3b8;
}

.duplicate-version-card-path {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: #64748b;
  margin-bottom: 6px;
}

.duplicate-version-card-path-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.duplicate-version-card-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.duplicate-version-meta-item {
  display: flex;
  align-items: center;
  gap: 3px;
  font-size: 11px;
  color: #94a3b8;
}

/* ── 暗色模式 ── */
:global(html.kikoerumanager-dark) .duplicate-detail-panel {
  background: var(--km-dark-card, #1a1b2e);
  border-color: var(--km-dark-border, #2a2b3d);
}

:global(html.kikoerumanager-dark) .duplicate-detail-title,
:global(html.kikoerumanager-dark) .duplicate-version-card-name {
  color: #e2e8f0;
}

:global(html.kikoerumanager-dark) .duplicate-detail-subtitle,
:global(html.kikoerumanager-dark) .duplicate-version-card-path,
:global(html.kikoerumanager-dark) .duplicate-version-meta-item {
  color: #94a3b8;
}

:global(html.kikoerumanager-dark) .duplicate-detail-head,
:global(html.kikoerumanager-dark) .duplicate-detail-body {
  border-color: var(--km-dark-border, #2a2b3d);
}

:global(html.kikoerumanager-dark) .duplicate-detail-back {
  background: var(--km-dark-card, #1a1b2e);
  border-color: var(--km-dark-border, #2a2b3d);
  color: #94a3b8;
}

:global(html.kikoerumanager-dark) .duplicate-detail-back:hover {
  background: var(--km-dark-hover, rgba(255,255,255,0.06));
  color: #e2e8f0;
}

:global(html.kikoerumanager-dark) .duplicate-version-card {
  border-color: var(--km-dark-border, #2a2b3d);
}

:global(html.kikoerumanager-dark) .duplicate-version-card:hover {
  border-color: rgba(139, 92, 246, 0.3);
  background: rgba(139, 92, 246, 0.06);
}

:global(html.kikoerumanager-dark) .duplicate-version-card.is-selected {
  border-color: #8b5cf6;
  background: rgba(139, 92, 246, 0.12);
}

:global(html.kikoerumanager-dark) .duplicate-error-alert {
  background: rgba(239, 68, 68, 0.1);
  border-color: rgba(239, 68, 68, 0.25);
}

:global(html.kikoerumanager-dark) .duplicate-card-tags .tag-chip.is-size {
  background: rgba(255,255,255,0.06);
  color: #64748b;
}

:global(html.kikoerumanager-dark) .duplicate-detail-tabs {
  border-color: var(--km-dark-border, #2a2b3d);
}

:global(html.kikoerumanager-dark) .duplicate-detail-tab {
  color: #94a3b8;
}

:global(html.kikoerumanager-dark) .duplicate-version-files {
  background: rgba(255, 255, 255, 0.03);
  border-color: var(--km-dark-border, #2a2b3d);
}

:global(html.kikoerumanager-dark) .duplicate-version-file-row {
  color: #cbd5e1;
}

:global(html.kikoerumanager-dark) .duplicate-version-file-row svg {
  color: #64748b;
}

:global(html.kikoerumanager-dark) .duplicate-version-file-row svg.is-dir {
  color: #8b5cf6;
}

:global(html.kikoerumanager-dark) .duplicate-version-files-truncated {
  background: rgba(245, 158, 11, 0.1);
  border-color: rgba(245, 158, 11, 0.2);
}

:global(html.kikoerumanager-dark) .duplicate-diff-chip {
  background: var(--km-dark-card, #1a1b2e);
  border-color: var(--km-dark-border, #2a2b3d);
  color: #94a3b8;
}

:global(html.kikoerumanager-dark) .duplicate-diff-chip:hover {
  border-color: rgba(139, 92, 246, 0.4);
  color: #a78bfa;
}

:global(html.kikoerumanager-dark) .duplicate-diff-chip.is-active {
  background: rgba(139, 92, 246, 0.16);
  border-color: rgba(139, 92, 246, 0.5);
  color: #c4b5fd;
}

:global(html.kikoerumanager-dark) .duplicate-diff-row:hover {
  background: rgba(255, 255, 255, 0.04);
}

:global(html.kikoerumanager-dark) .duplicate-diff-path {
  color: #cbd5e1;
}

:global(html.kikoerumanager-dark) .duplicate-diff-empty {
  color: #64748b;
}

/* ── 响应式 ── */
@media (max-width: 900px) {
  .duplicate-detail-panel {
    width: 360px;
  }

  .duplicate-detail-panel.is-wide {
    width: 480px;
  }

  .duplicate-card-grid {
    grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  }
}

@media (max-width: 640px) {
  .duplicate-detail-panel {
    position: fixed;
    top: 0;
    right: 0;
    bottom: 0;
    width: 100%;
    max-width: 100%;
    z-index: 100;
    border-radius: 0;
    margin-left: 0;
  }

  .duplicate-card-grid {
    grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
    gap: 8px;
  }
}
</style>
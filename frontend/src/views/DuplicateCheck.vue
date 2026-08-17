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
                    <HardDrive :size="11" />
                    {{ group.library_count }} 个库存
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
        <div v-if="activeRjcode" class="duplicate-detail-panel">
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

            <!-- 版本列表 -->
            <div class="duplicate-detail-body">
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
                  <div class="duplicate-version-card-name">{{ version.root_name || version.library_name }}</div>
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
                      {{ version.entry_count }} 个文件
                    </span>
                    <span v-if="version.max_mtime" class="duplicate-version-meta-item">
                      <Clock :size="11" />
                      {{ formatDate(version.max_mtime) }}
                    </span>
                  </div>
                </div>
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
  ArrowLeft,
  Check,
  CheckCircle2,
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
  font-weight: 600;
  font-size: 13px;
  color: #1e293b;
  margin-bottom: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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

/* ── 响应式 ── */
@media (max-width: 900px) {
  .duplicate-detail-panel {
    width: 360px;
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
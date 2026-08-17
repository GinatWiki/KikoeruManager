<template>
  <div class="duplicate-check-page">
    <AppPageHeader
      :icon="GitCompare"
      icon-color="var(--km-nav-duplicate-icon, #8b5cf6)"
      title="仓库查重"
      subtitle="检测库存中同一 RJ 号的重复版本，比对后选择保留或删除"
    >
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
    </AppPageHeader>

    <div v-if="errorMessage" class="duplicate-error-alert">
      <AlertCircle class="w-5 h-5 flex-shrink-0 mt-0.5 text-red-500" />
      <div>
        <h3 class="font-medium">获取重复列表失败</h3>
        <p class="text-sm mt-1 opacity-90">{{ errorMessage }}</p>
      </div>
    </div>

    <div class="duplicate-main" v-if="!loading || groups.length > 0">
      <template v-if="groups.length === 0 && !loading">
        <div class="duplicate-empty">
          <AppEmptyState description="没有发现重复版本，库存中的 RJ 号都是唯一的" size="lg" />
        </div>
      </template>
      <template v-else>
        <div class="duplicate-pane duplicate-list-pane">
          <div class="duplicate-list-header">
            <div class="duplicate-list-search">
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
          </div>

          <div class="duplicate-list-body">
            <div
              v-for="group in groups"
              :key="group.rjcode"
              class="duplicate-group-card"
              :class="{ 'is-active': activeRjcode === group.rjcode }"
              @click="selectGroup(group.rjcode)"
            >
              <div class="duplicate-group-card-head">
                <span class="duplicate-group-rjcode">{{ group.rjcode }}</span>
                <span class="duplicate-group-count">{{ group.version_count }} 个版本</span>
              </div>
              <div class="duplicate-group-card-meta">
                <span class="duplicate-group-meta-item">
                  <HardDrive :size="12" />
                  {{ group.library_count }} 个库存
                </span>
                <span class="duplicate-group-meta-item">
                  <Folder :size="12" />
                  {{ formatSize(group.total_size) }}
                </span>
              </div>
              <div v-if="group.names.length" class="duplicate-group-card-names">
                <span v-for="(name, idx) in group.names" :key="idx" class="duplicate-group-name-tag">{{ name }}</span>
              </div>
            </div>
          </div>

          <div class="duplicate-list-footer">
            <el-pagination
              small
              layout="prev, pager, next"
              :total="total"
              :page-size="pageSize"
              :current-page="currentPage"
              @current-change="handlePageChange"
            />
          </div>
        </div>

        <div class="duplicate-pane duplicate-detail-pane">
          <template v-if="!activeRjcode">
            <div class="duplicate-detail-placeholder">
              <AppEmptyState description="选择左侧重复分组查看详情" size="md" />
            </div>
          </template>
          <template v-else-if="detailLoading">
            <div class="duplicate-detail-loading">
              <AppLoadingAnimation variant="inline" :size="24" />
              <span>加载中...</span>
            </div>
          </template>
          <template v-else>
            <div class="duplicate-detail-head">
              <div class="duplicate-detail-head-left">
                <h3 class="duplicate-detail-title">{{ activeRjcode }}</h3>
                <span class="duplicate-detail-subtitle">{{ detailEntries.length }} 个版本</span>
              </div>
              <div class="duplicate-detail-head-actions">
                <StatefulButton
                  class="duplicate-keep-btn"
                  type="button"
                  unstyled
                  :show-default-icons="false"
                  :disabled="!selectedEntryIds.size || batchRunning"
                  :loading="batchRunning"
                  @click="handleKeepSelected"
                >
                  <template #prefix>
                    <Check :size="14" :stroke-width="2.5" />
                  </template>
                  保留选中 ({{ selectedEntryIds.size }})
                </StatefulButton>
              </div>
            </div>

            <div class="duplicate-detail-body">
              <div
                v-for="entry in detailEntries"
                :key="entry.id"
                class="duplicate-version-card"
                :class="{ 'is-selected': selectedEntryIds.has(entry.id) }"
                @click="toggleEntrySelection(entry.id)"
              >
                <div class="duplicate-version-card-check">
                  <div class="duplicate-version-radio" :class="{ 'is-checked': selectedEntryIds.has(entry.id) }">
                    <Check v-if="selectedEntryIds.has(entry.id)" :size="12" :stroke-width="3" />
                  </div>
                </div>
                <div class="duplicate-version-card-body">
                  <div class="duplicate-version-card-name">{{ entry.name }}</div>
                  <div class="duplicate-version-card-path">
                    <FolderTree :size="12" />
                    <span class="duplicate-version-card-path-text" :title="entry.absolute_path">{{ entry.relative_path }}</span>
                  </div>
                  <div class="duplicate-version-card-meta">
                    <span class="duplicate-version-meta-item">
                      <HardDrive :size="11" />
                      {{ entry.library_name }}
                    </span>
                    <span class="duplicate-version-meta-item">
                      <Folder :size="11" />
                      {{ formatSize(entry.size) }}
                    </span>
                    <span v-if="entry.mtime" class="duplicate-version-meta-item">
                      <Clock :size="11" />
                      {{ formatDate(entry.mtime) }}
                    </span>
                    <span class="duplicate-version-meta-item duplicate-version-type" :class="`is-${entry.entry_type}`">
                      {{ entry.entry_type === 'dir' ? '目录' : '文件' }}
                    </span>
                  </div>
                </div>
              </div>
            </div>
          </template>
        </div>
      </template>
    </div>

    <div v-else-if="loading" class="duplicate-loading">
      <AppLoadingAnimation variant="inline" :size="28" />
      <span>正在扫描重复版本...</span>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import {
  AlertCircle,
  Check,
  CheckCircle2,
  Clock,
  Folder,
  FolderTree,
  GitCompare,
  HardDrive,
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
import StatefulButton from '../components/ui/stateful-button.vue'

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

const activeRjcode = ref('')
const detailLoading = ref(false)
const detailEntries = ref([])
const selectedEntryIds = ref(new Set())
const batchRunning = ref(false)

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

async function selectGroup(rjcode) {
  if (activeRjcode.value === rjcode) return
  activeRjcode.value = rjcode
  selectedEntryIds.value = new Set()
  detailLoading.value = true
  try {
    const data = await duplicateCheckApi.groupDetail(rjcode)
    detailEntries.value = data.entries || []
    await nextTick()
  } catch (err) {
    errorMessage.value = err?.response?.data?.detail || err.message || '获取版本详情失败'
  } finally {
    detailLoading.value = false
  }
}

function toggleEntrySelection(entryId) {
  const next = new Set(selectedEntryIds.value)
  if (next.has(entryId)) {
    next.delete(entryId)
  } else {
    next.add(entryId)
  }
  selectedEntryIds.value = next
}

async function handleKeepSelected() {
  if (!selectedEntryIds.value.size) return
  const rjcode = activeRjcode.value
  const keepIds = [...selectedEntryIds.value]
  const deleteCount = detailEntries.value.length - keepIds.length

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
    message: `将保留 ${keepIds.length} 个选中版本，删除其余 ${deleteCount} 个版本。此操作不可撤销，确定继续？`,
    confirmText: '确认删除',
    confirmType: 'danger',
  })

  if (!confirmed) return

  batchRunning.value = true
  try {
    const result = await duplicateCheckApi.keep(rjcode, keepIds)
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

    activeRjcode.value = ''
    detailEntries.value = []
    selectedEntryIds.value = new Set()
    await fetchGroups()
  } catch (err) {
    errorMessage.value = err?.response?.data?.detail || err.message || '删除操作失败'
  } finally {
    batchRunning.value = false
  }
}

function handlePageChange(page) {
  currentPage.value = page
  fetchGroups()
}

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
.duplicate-check-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  padding: 0 16px 16px;
}

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

.duplicate-main {
  display: flex;
  flex: 1;
  gap: 12px;
  min-height: 0;
  overflow: hidden;
}

.duplicate-empty {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}

.duplicate-loading {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  color: #94a3b8;
  font-size: 14px;
}

.duplicate-pane {
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.duplicate-list-pane {
  width: 320px;
  flex-shrink: 0;
  border-radius: 12px;
  border: 1px solid #e2e8f0;
  background: #fff;
}

.duplicate-list-header {
  display: flex;
  gap: 8px;
  padding: 12px;
  border-bottom: 1px solid #f1f5f9;
}

.duplicate-list-search {
  flex: 1;
  position: relative;
}

.duplicate-search-icon {
  position: absolute;
  left: 10px;
  top: 50%;
  transform: translateY(-50%);
  color: #94a3b8;
  z-index: 1;
}

.duplicate-list-search :deep(.el-input__wrapper) {
  padding-left: 30px;
}

.duplicate-list-body {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
}

.duplicate-list-footer {
  padding: 8px 12px;
  border-top: 1px solid #f1f5f9;
  display: flex;
  justify-content: center;
}

.duplicate-group-card {
  padding: 10px 12px;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.15s;
  border: 1px solid transparent;
  margin-bottom: 4px;
}

.duplicate-group-card:hover {
  background: #f8fafc;
  border-color: #e2e8f0;
}

.duplicate-group-card.is-active {
  background: #f5f3ff;
  border-color: #c4b5fd;
}

.duplicate-group-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}

.duplicate-group-rjcode {
  font-weight: 700;
  font-size: 13px;
  color: #1e293b;
  font-family: 'SF Mono', 'Cascadia Code', monospace;
}

.duplicate-group-count {
  font-size: 11px;
  color: #8b5cf6;
  font-weight: 600;
  background: rgba(139, 92, 246, 0.08);
  padding: 1px 8px;
  border-radius: 100px;
}

.duplicate-group-card-meta {
  display: flex;
  gap: 12px;
  font-size: 11px;
  color: #64748b;
}

.duplicate-group-meta-item {
  display: flex;
  align-items: center;
  gap: 3px;
}

.duplicate-group-card-names {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 6px;
}

.duplicate-group-name-tag {
  font-size: 10px;
  color: #94a3b8;
  background: #f1f5f9;
  padding: 1px 6px;
  border-radius: 4px;
  max-width: 140px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.duplicate-detail-pane {
  flex: 1;
  border-radius: 12px;
  border: 1px solid #e2e8f0;
  background: #fff;
  min-width: 0;
}

.duplicate-detail-placeholder,
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
  align-items: center;
  justify-content: space-between;
  padding: 14px 16px;
  border-bottom: 1px solid #f1f5f9;
}

.duplicate-detail-head-left {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.duplicate-detail-title {
  font-size: 16px;
  font-weight: 700;
  color: #1e293b;
  margin: 0;
  font-family: 'SF Mono', 'Cascadia Code', monospace;
}

.duplicate-detail-subtitle {
  font-size: 12px;
  color: #64748b;
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

.duplicate-version-type {
  padding: 0 6px;
  border-radius: 4px;
  font-weight: 600;
}

.duplicate-version-type.is-dir {
  background: rgba(59, 130, 246, 0.08);
  color: #3b82f6;
}

.duplicate-version-type.is-file {
  background: rgba(245, 158, 11, 0.08);
  color: #f59e0b;
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

/* 暗色模式 */
:global(html.kikoerumanager-dark) .duplicate-list-pane,
:global(html.kikoerumanager-dark) .duplicate-detail-pane {
  background: var(--km-dark-card, #1a1b2e);
  border-color: var(--km-dark-border, #2a2b3d);
}

:global(html.kikoerumanager-dark) .duplicate-group-card:hover {
  background: var(--km-dark-hover, rgba(255,255,255,0.04));
  border-color: var(--km-dark-border, #2a2b3d);
}

:global(html.kikoerumanager-dark) .duplicate-group-card.is-active {
  background: rgba(139, 92, 246, 0.12);
  border-color: rgba(139, 92, 246, 0.3);
}

:global(html.kikoerumanager-dark) .duplicate-group-rjcode,
:global(html.kikoerumanager-dark) .duplicate-detail-title,
:global(html.kikoerumanager-dark) .duplicate-version-card-name {
  color: #e2e8f0;
}

:global(html.kikoerumanager-dark) .duplicate-group-card-meta,
:global(html.kikoerumanager-dark) .duplicate-version-card-path,
:global(html.kikoerumanager-dark) .duplicate-version-meta-item {
  color: #94a3b8;
}

:global(html.kikoerumanager-dark) .duplicate-list-header,
:global(html.kikoerumanager-dark) .duplicate-detail-head,
:global(html.kikoerumanager-dark) .duplicate-list-footer {
  border-color: var(--km-dark-border, #2a2b3d);
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

:global(html.kikoerumanager-dark) .duplicate-group-name-tag {
  background: rgba(255,255,255,0.06);
  color: #64748b;
}

:global(html.kikoerumanager-dark) .duplicate-error-alert {
  background: rgba(239, 68, 68, 0.1);
  border-color: rgba(239, 68, 68, 0.25);
}
</style>
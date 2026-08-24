<template>
  <section
    class="dashboard-deferred flex max-h-[38%] min-h-0 flex-shrink-0 flex-col rounded-[14px] border border-slate-200/80 bg-white p-3.5 shadow-[0_2px_8px_-6px_rgba(15,23,42,0.08)]"
    data-section="dashboard-deferred-archive"
  >
    <header class="flex flex-shrink-0 items-center justify-between gap-2">
      <div class="flex min-w-0 items-center gap-2.5">
        <Hourglass :size="18" :stroke-width="2" class="flex-shrink-0 text-slate-700" />
        <div class="min-w-0 leading-tight">
          <h2 class="m-0 text-[14px] font-bold tracking-tight text-slate-900">延后归档队列</h2>
          <p class="m-0 mt-0.5 text-[11.5px] text-slate-500">
            {{ subtitle }}
          </p>
        </div>
      </div>
      <button
        type="button"
        class="dash-deferred-refresh-btn group"
        :disabled="loading"
        title="刷新队列"
        @click="refresh()"
      >
        <RefreshCw
          :size="13"
          :stroke-width="2.2"
          :class="loading ? 'animate-spin' : 'transition-transform duration-300 group-hover:rotate-180'"
        />
      </button>
    </header>

    <div v-if="jobs.length" class="mt-2 flex min-h-0 flex-1 flex-col gap-1.5 overflow-y-auto pr-0.5">
      <article
        v-for="job in jobs"
        :key="job.job_id"
        class="group grid grid-cols-[minmax(0,1fr)_auto] items-start gap-2 rounded-[9px] border border-slate-100 bg-white px-2.5 py-2 transition-colors duration-200 hover:border-slate-200 hover:bg-slate-50/60"
      >
        <div class="min-w-0">
          <div class="flex items-center gap-1.5">
            <span class="text-[12px] font-bold tabular-nums text-slate-800">{{ job.rjcode || '—' }}</span>
            <span class="dash-deferred-status-chip" :class="`dash-deferred-status-chip--${job.status}`">
              {{ STATUS_LABEL[job.status] || job.status }}
            </span>
            <span
              v-if="job.cancel_requested && !isDone(job)"
              class="text-[10.5px] font-medium text-slate-400"
            >取消中</span>
          </div>
          <div class="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-slate-500">
            <span :title="`入队时间 ${formatDateTime(job.created_at)}`">入队 {{ shortTime(job.created_at) }}</span>
            <span
              v-if="!isDone(job) && job.available_at"
              :title="`预计可执行 ${formatDateTime(job.available_at)}（需前台任务空闲且系统静默后执行）`"
            >可执行 {{ shortTime(job.available_at) }}</span>
            <span v-if="job.attempt_count > 0">已试 {{ job.attempt_count }} 次</span>
            <span v-if="job.completed_at" title="完成时间">{{ shortTime(job.completed_at) }} 完成</span>
          </div>
          <p
            v-if="job.last_error"
            class="m-0 mt-1 line-clamp-2 break-all text-[11px] leading-snug text-rose-600"
            :title="job.last_error"
          >{{ job.last_error }}</p>
        </div>

        <div class="flex flex-shrink-0 items-center gap-1 opacity-0 transition-opacity duration-200 group-hover:opacity-100">
          <button
            v-if="job.status === 'failed'"
            type="button"
            class="dash-deferred-action-btn"
            :disabled="actingId === job.job_id"
            title="重试"
            @click="retryJob(job)"
          >
            <RotateCcw :size="12" :stroke-width="2.4" />
          </button>
          <button
            v-if="!isDone(job) && job.status !== 'processing'"
            type="button"
            class="dash-deferred-action-btn dash-deferred-action-btn--danger"
            :disabled="actingId === job.job_id"
            title="取消归档（保留源压缩包）"
            @click="cancelJob(job)"
          >
            <XCircle :size="12" :stroke-width="2.4" />
          </button>
        </div>
      </article>
    </div>

    <div v-else class="mt-2 flex flex-1 items-center justify-center overflow-hidden">
      <p class="m-0 text-center text-[11.5px] leading-relaxed text-slate-400">
        队列为空<br />任务解压完成后，源压缩包会在系统空闲时在此排队归档
      </p>
    </div>
  </section>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Hourglass, RefreshCw, RotateCcw, XCircle } from 'lucide-vue-next'
import { deferredArchiveApi } from '../../api'
import { useRealtimeEvents } from '../../composables/useRealtimeEvents'

const STATUS_LABEL = {
  pending: '等待空闲',
  processing: '归档中',
  waiting_retry: '等待重试',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

const jobs = ref([])
const total = ref(0)
const pendingCount = ref(0)
const loading = ref(false)
const actingId = ref(null)

const isDone = (job) => ['completed', 'cancelled', 'failed'].includes(job?.status)

const subtitle = computed(() => {
  if (!jobs.value.length) return '暂无待归档作业'
  if (pendingCount.value > 0) return `${pendingCount.value} 个作业等待空闲归档`
  return `共 ${total.value} 条记录`
})

function shortTime(value) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  const now = new Date()
  const sameDay = date.toDateString() === now.toDateString()
  const hm = `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
  if (sameDay) return hm
  return `${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')} ${hm}`
}

function formatDateTime(value) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleString()
}

async function refresh({ silent } = {}) {
  if (!silent) loading.value = true
  try {
    const data = await deferredArchiveApi.list({ limit: 50 })
    jobs.value = Array.isArray(data?.jobs) ? data.jobs : []
    total.value = Number(data?.total) || 0
    pendingCount.value = Number(data?.pending_count) || 0
  } catch (err) {
    if (!silent) console.error('[延后归档队列] 拉取失败', err)
  } finally {
    if (!silent) loading.value = false
  }
}

async function retryJob(job) {
  actingId.value = job.job_id
  try {
    await deferredArchiveApi.retry(job.job_id)
    ElMessage.success(`RJ${job.rjcode || ''} 已重新入队`)
    await refresh({ silent: true })
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || '重试失败')
  } finally {
    actingId.value = null
  }
}

async function cancelJob(job) {
  actingId.value = job.job_id
  try {
    await deferredArchiveApi.cancel(job.job_id)
    ElMessage.success('已请求取消，源压缩包将保留')
    await refresh({ silent: true })
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || '取消失败')
  } finally {
    actingId.value = null
  }
}

// 实时刷新：订阅延后归档广播事件，防抖避免连续归档时频繁拉取
const realtime = useRealtimeEvents()
let unsubscribe = null
let refreshTimer = null

function scheduleSilentRefresh() {
  if (refreshTimer) clearTimeout(refreshTimer)
  refreshTimer = setTimeout(() => {
    refreshTimer = null
    refresh({ silent: true })
  }, 600)
}

onMounted(() => {
  refresh()
  realtime.start()
  unsubscribe = realtime.subscribe('archive.queue.changed', scheduleSilentRefresh)
})

onUnmounted(() => {
  if (unsubscribe) {
    unsubscribe()
    unsubscribe = null
  }
  if (refreshTimer) {
    clearTimeout(refreshTimer)
    refreshTimer = null
  }
  realtime.stop()
})
</script>

<style scoped>
.dash-deferred-status-chip {
  display: inline-flex;
  align-items: center;
  height: 18px;
  padding: 0 6px;
  border-radius: 5px;
  font-size: 10.5px;
  font-weight: 700;
  line-height: 1;
  white-space: nowrap;
}
.dash-deferred-status-chip--completed { background: rgba(16, 185, 129, 0.12); color: rgb(4 120 87); }
.dash-deferred-status-chip--failed { background: rgba(244, 63, 94, 0.12); color: rgb(190 18 60); }
.dash-deferred-status-chip--processing,
.dash-deferred-status-chip--waiting_retry { background: rgba(245, 158, 11, 0.12); color: rgb(180 83 9); }
.dash-deferred-status-chip--pending { background: rgba(100, 116, 139, 0.12); color: rgb(71 85 105); }
.dash-deferred-status-chip--cancelled { background: rgba(148, 163, 184, 0.16); color: rgb(71 85 105); }

.dash-deferred-refresh-btn,
.dash-deferred-action-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  padding: 0;
  border: 1px solid rgba(148, 163, 184, 0.48);
  border-radius: 7px;
  background: rgba(255, 255, 255, 0.95);
  color: rgb(71 85 105);
  cursor: pointer;
  transition: all 0.25s cubic-bezier(0.34, 1.56, 0.64, 1);
}
.dash-deferred-refresh-btn:hover,
.dash-deferred-action-btn:hover {
  transform: translateY(-1px);
  border-color: rgba(100, 116, 139, 0.62);
  color: rgb(15 23 42);
}
.dash-deferred-action-btn--danger:hover {
  border-color: rgba(244, 63, 94, 0.45);
  color: rgb(190 18 60);
}
.dash-deferred-refresh-btn:disabled,
.dash-deferred-action-btn:disabled {
  cursor: wait;
  opacity: 0.5;
  pointer-events: none;
}

:global(html.kikoerumanager-dark) .dashboard-deferred {
  background: var(--km-dark-surface, rgba(24, 24, 27, 0.92));
  border-color: var(--km-dark-border, rgba(255, 255, 255, 0.09));
}
:global(html.kikoerumanager-dark) .dashboard-deferred h2 {
  color: #f4f4f5 !important;
}
:global(html.kikoerumanager-dark) .dashboard-deferred p,
:global(html.kikoerumanager-dark) .dashboard-deferred span:not(.dash-deferred-status-chip) {
  color: rgba(228, 228, 231, 0.78) !important;
}
:global(html.kikoerumanager-dark) .dashboard-deferred article {
  background: var(--km-dark-surface-soft, rgba(255, 255, 255, 0.04)) !important;
  border-color: var(--km-dark-border, rgba(255, 255, 255, 0.08)) !important;
}
:global(html.kikoerumanager-dark) .dashboard-deferred .dash-deferred-status-chip--completed { background: rgba(16, 185, 129, 0.16); color: #d1fae5; }
:global(html.kikoerumanager-dark) .dashboard-deferred .dash-deferred-status-chip--failed { background: rgba(244, 63, 94, 0.16); color: #fecdd3; }
:global(html.kikoerumanager-dark) .dashboard-deferred .dash-deferred-status-chip--processing,
:global(html.kikoerumanager-dark) .dashboard-deferred .dash-deferred-status-chip--waiting_retry { background: rgba(245, 158, 11, 0.16); color: #fde68a; }
:global(html.kikoerumanager-dark) .dashboard-deferred .dash-deferred-status-chip--pending,
:global(html.kikoerumanager-dark) .dashboard-deferred .dash-deferred-status-chip--cancelled { background: rgba(255, 255, 255, 0.08); color: #e2e8f0; }
:global(html.kikoerumanager-dark) .dashboard-deferred .dash-deferred-refresh-btn,
:global(html.kikoerumanager-dark) .dashboard-deferred .dash-deferred-action-btn {
  background: var(--km-dark-surface-soft, rgba(255, 255, 255, 0.05)) !important;
  border-color: var(--km-dark-border, rgba(255, 255, 255, 0.12)) !important;
  color: var(--km-dark-text-strong, #fff) !important;
}
</style>

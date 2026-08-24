<script setup>
import { computed } from 'vue'
import { AlertCircle, Check, X } from 'lucide-vue-next'
import animeShareIcon from '../../assets/platforms/anime-sharing.png'
import southPlusIcon from '../../assets/platforms/south-plus.ico'
import asmrOneIcon from '../../assets/platforms/asmr-one.svg'

const props = defineProps({
  item: { type: Object, required: true },
  /** 语言版本行（{rjcode, group_key, group_short_label, title}），
   *  传入后按该版本的 RJ 展示独立的三来源检索状态。 */
  variant: { type: Object, default: null },
})

const emit = defineEmits(['open'])

const sourceMeta = [
  { key: 'anime_share', label: 'AnimeShare', icon: animeShareIcon },
  { key: 'south_plus', label: '南+', icon: southPlusIcon },
  { key: 'asmr_one', label: 'asmr.one', icon: asmrOneIcon },
]

function normalizeRj(value) {
  return String(value || '').trim().toUpperCase()
}

function canonicalRjcode() {
  return normalizeRj(
    props.item?.canonical_rjcode
      || props.item?.display_rjcode
      || props.item?.rjcode
      || '',
  )
}

function variantRjcode() {
  return normalizeRj(props.variant?.rjcode || '') || canonicalRjcode()
}

function entryRjcode(entry) {
  return normalizeRj(entry?.rjcode || '')
}

// asmr.one 不依赖外部搜索接口：直接复用社团补全已有的探测结果
// （has_asmr_one / asmr_available_rjcode，与 ASMR 同步下载同一套探测）。
function asmrOnePayload() {
  const availableRj = normalizeRj(
    props.item?.asmr_available_rjcode
      || props.item?.sourceCompare?.asmr_one?.primary_rjcode
      || props.item?.source_compare?.asmr_one?.primary_rjcode
      || '',
  )
  const targetRj = props.variant ? variantRjcode() : (availableRj || canonicalRjcode())
  let hit = false
  if (props.variant) {
    // 版本行模式：只有探测到的可用 RJ 与本行 RJ 一致才算命中
    hit = Boolean(props.item?.has_asmr_one && availableRj && availableRj === targetRj)
  } else {
    hit = Boolean(props.item?.has_asmr_one && targetRj)
  }
  return {
    status: hit ? 'hit' : 'miss',
    results: targetRj ? [{
      source: 'asmr_one',
      rjcode: targetRj,
      variant_key: props.variant?.group_key || 'original',
      variant_label: props.variant?.group_short_label || '原作',
      title: hit ? `在 asmr.one 打开 ${targetRj}` : `查看 asmr.one 作品页 ${targetRj}`,
      url: `https://asmr.one/work/${targetRj}`,
    }] : [],
  }
}

function fallbackSearchResult(source) {
  const rjcode = variantRjcode()
  if (!/^RJ(?:\d{6}|\d{8})$/.test(rjcode)) return null
  let url = ''
  if (source === 'anime_share') {
    url = `https://www.anime-sharing.com/search/3528560/?q=${encodeURIComponent(rjcode)}&o=relevance`
  } else if (source === 'south_plus') {
    const params = new URLSearchParams({
      step: '2',
      keyword: rjcode,
      method: 'OR',
      pwuser: '',
      sch_area: '0',
      f_fid: 'all',
      sch_time: 'all',
      orderway: 'postdate',
      asc: 'DESC',
    })
    url = `https://bbs.south-plus.net/search.php?${params.toString()}`
  } else {
    url = `https://asmr.one/work/${rjcode}`
  }
  return {
    source,
    rjcode,
    variant_key: props.variant?.group_key || 'original',
    variant_label: props.variant?.group_short_label || '原作',
    title: `搜索 ${rjcode}`,
    url,
  }
}

// 版本行模式：优先读取后端按版本下发的 sources 载荷；
// 旧缓存 / 请求失败只有合并载荷时，按本行 RJ 过滤合并结果。
function variantSourcePayload(sourceKey) {
  const external = props.item?.external_search || {}
  if (!props.variant) return external[sourceKey] || {}
  const targetRj = variantRjcode()
  const list = Array.isArray(external.variants) ? external.variants : []
  const matched = list.find(item => normalizeRj(item?.rjcode) === targetRj)
  if (matched?.sources?.[sourceKey]) return matched.sources[sourceKey]
  const merged = external[sourceKey] || {}
  const results = (Array.isArray(merged.results) ? merged.results : [])
    .filter(entry => entryRjcode(entry) === targetRj)
  const searchResults = (Array.isArray(merged.search_results) ? merged.search_results : [])
    .filter(entry => entryRjcode(entry) === targetRj)
  if (results.length || searchResults.length) {
    return { ...merged, results, search_results: searchResults }
  }
  if (merged.status) {
    // 其它版本命中不代表本版本；没有本版本记录时按未找到处理
    const status = merged.status === 'loading' || merged.status === 'pending'
      ? merged.status
      : 'miss'
    return { status, results: [], search_results: [], search_url: merged.search_url || '' }
  }
  return {}
}

const sources = computed(() => sourceMeta.map(meta => {
  if (meta.key === 'asmr_one') {
    const payload = asmrOnePayload()
    return { ...meta, status: payload.status, results: payload.results, actions: payload.results }
  }
  const payload = variantSourcePayload(meta.key)
  const status = String(payload.status || 'loading')
  const results = Array.isArray(payload.results) ? payload.results : []
  const searchResults = Array.isArray(payload.search_results) ? payload.search_results : []
  // 后端按自定义域名动态生成 search_url，优先使用；只有完全缺失时才本地兜底
  const serverFallbackUrl = String(payload.search_url || '').trim()
  const localFallback = status !== 'loading' ? fallbackSearchResult(meta.key) : null
  const fallback = localFallback && serverFallbackUrl
    ? { ...localFallback, url: serverFallbackUrl }
    : localFallback
  const actions = status === 'hit' && results.length
    ? results
    : (searchResults.length ? searchResults : (fallback ? [fallback] : []))
  return { ...meta, status, results, actions }
}))

function statusIcon(status) {
  if (status === 'hit') return Check
  if (status === 'miss') return X
  if (status === 'unavailable' || status === 'error') return AlertCircle
  return null
}

function statusTitle(entry) {
  if (entry.status === 'hit') return `${entry.label} · 命中 ${entry.results.length} 个结果`
  if (entry.status === 'miss') return `${entry.label} · 未找到，点击打开搜索页`
  if (entry.status === 'pending') return `${entry.label} · 已入队，后台探测中`
  if (entry.status === 'unavailable') return `${entry.label} · 当前无法探测，点击打开搜索页`
  if (entry.status === 'error') return `${entry.label} · 查询失败，点击打开搜索页`
  return `${entry.label} · 查询中`
}

function handleOpen(entry) {
  if (!entry.actions.length) return
  emit('open', {
    item: props.item,
    source: entry.key,
    status: entry.status,
    results: entry.actions,
  })
}
</script>

<template>
  <span class="external-source-chips" @click.stop>
    <button
      v-for="entry in sources"
      :key="entry.key"
      type="button"
      class="external-source-chip"
      :class="`is-${entry.status}`"
      :title="statusTitle(entry)"
      :aria-label="statusTitle(entry)"
      :disabled="!entry.actions.length"
      @click.stop="handleOpen(entry)"
    >
      <img :src="entry.icon" :alt="entry.label" />
      <span class="external-source-status" aria-hidden="true">
        <component :is="statusIcon(entry.status)" v-if="statusIcon(entry.status)" :size="7" :stroke-width="3" />
      </span>
    </button>
  </span>
</template>

<style scoped>
.external-source-chips {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 3px;
  padding-right: 4px;
}

.external-source-chip {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 22px;
  flex: 0 0 24px;
  padding: 2px 3px;
  border: 0;
  border-radius: 0;
  background: transparent;
  cursor: pointer;
  transition: all .2s cubic-bezier(.34,1.56,.64,1);
}

.external-source-chip img {
  display: block;
  width: 16px;
  height: 16px;
  object-fit: contain;
}

.external-source-chip:hover:not(:disabled) {
  transform: translateY(-1px) scale(1.05);
}

.external-source-chip:active:not(:disabled) { transform: scale(.94); }

.external-source-chip.is-hit {
  background: transparent;
  box-shadow: none;
}

.external-source-chip.is-miss img { filter: grayscale(1); opacity: .58; }
.external-source-chip.is-unavailable img { filter: grayscale(.75); opacity: .64; }
.external-source-chip.is-error img { filter: grayscale(.45); opacity: .72; }
.external-source-chip.is-loading img { opacity: .68; }

.external-source-chip:disabled {
  cursor: default;
}

.external-source-status {
  position: absolute;
  top: -4px;
  right: -4px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 11px;
  height: 11px;
  border: 1px solid #fff;
  border-radius: 999px;
  background: #94a3b8;
  color: #fff;
}

.is-hit .external-source-status { background: #22c55e; }
.is-miss .external-source-status { background: #94a3b8; }
.is-unavailable .external-source-status { background: #f59e0b; }
.is-error .external-source-status { background: #ef4444; }
.is-pending .external-source-status { animation: externalSourcePulse 1s ease-in-out infinite; }
.is-loading .external-source-status { animation: externalSourcePulse 1s ease-in-out infinite; }

@keyframes externalSourcePulse {
  0%, 100% { opacity: .45; transform: scale(.85); }
  50% { opacity: 1; transform: scale(1); }
}

:global(html.kikoerumanager-dark .external-source-chip.is-hit),
:global(body.kikoerumanager-dark .external-source-chip.is-hit) {
  background: transparent;
}

:global(html.kikoerumanager-dark .external-source-status),
:global(body.kikoerumanager-dark .external-source-status) {
  border-color: #18181b;
}
</style>

import { ref, computed, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { subtitleImportApi, configApi } from '../api'

/**
 * 压缩包补配 - 手动扫描入口。
 *
 * 预检单列表只包含自动处理流程产生的记录；用户手头有字幕压缩包时，
 * 在这里输入路径做预检并直接导入（复用 /subtitle-import/archive/preview|import）。
 */
export function useSubtitleImportArchiveManual({
  getSubtitleWorkbenchFilterOptions,
  openImportedTask,
  candidateKey
}) {
  const manualArchivePath = ref('')
  const manualArchiveLoading = ref(false)
  const manualArchiveImporting = ref(false)
  const manualArchivePreview = ref(null)
  const manualCandidateSelection = ref('')

  // 与文件夹补配一致：默认填入设置中的 ASMR 字幕目录（storage.asmr_subtitle_path），
  // 用户仍可手动改成具体压缩包路径；加载失败时静默降级为空输入框
  void (async () => {
    try {
      const config = await configApi.get()
      const defaultPath = String(config?.storage?.asmr_subtitle_path || '').trim()
      if (defaultPath && !manualArchivePath.value) {
        manualArchivePath.value = defaultPath
      }
    } catch (error) {
      console.debug('[subtitle-import] 加载默认字幕目录失败', error)
    }
  })()

  const selectedManualCandidate = computed(() => {
    return (manualArchivePreview.value?.candidates || []).find(
      candidate => candidateKey(candidate) === manualCandidateSelection.value
    ) || null
  })

  const canExecuteManualArchiveImport = computed(() => {
    if (!manualArchivePreview.value) return false
    const readyCount = Number(manualArchivePreview.value.ready_candidate_count || 0)
    if (readyCount <= 0) return false
    return Boolean(selectedManualCandidate.value)
  })

  watch(manualArchivePreview, (preview) => {
    if (!preview) {
      manualCandidateSelection.value = ''
      return
    }
    const selected = preview.selected_candidate
    if (selected) {
      manualCandidateSelection.value = candidateKey(selected)
      return
    }
    const readyCandidates = (preview.candidates || []).filter(candidate => candidate?.ready_for_import)
    manualCandidateSelection.value = readyCandidates.length === 1 ? candidateKey(readyCandidates[0]) : ''
  }, { immediate: true })

  function clearManualArchivePreview() {
    manualArchivePreview.value = null
    manualCandidateSelection.value = ''
  }

  async function previewManualArchive() {
    const path = manualArchivePath.value.trim()
    if (!path) {
      ElMessage.warning('请先输入字幕压缩包路径')
      return
    }

    manualArchiveLoading.value = true
    try {
      const data = await subtitleImportApi.previewArchive(path)
      manualArchivePreview.value = data.preview || data
      ElMessage.success('字幕压缩包预检完成')
    } catch (error) {
      manualArchivePreview.value = null
      ElMessage.error('字幕压缩包预检失败: ' + (error.response?.data?.detail || error.message))
    } finally {
      manualArchiveLoading.value = false
    }
  }

  async function executeManualArchiveImport() {
    const path = manualArchivePath.value.trim()
    const candidate = selectedManualCandidate.value
    if (!path || !candidate) return false

    manualArchiveImporting.value = true
    // 兜底：极端情况下 axios 不抛 timeout 时强制清空 loading（与文件夹导入一致，15 分钟）
    const fallbackClearTimer = window.setTimeout(() => {
      if (manualArchiveImporting.value) {
        console.warn('[subtitle-import] 压缩包导入超过 15 分钟未返回，强制清空 loading 状态')
        manualArchiveImporting.value = false
      }
    }, 15 * 60 * 1000)
    try {
      const filterOptions = getSubtitleWorkbenchFilterOptions()
      const data = await subtitleImportApi.importArchive(path, {
        targetLibraryId: candidate.library_id,
        targetFolderPath: candidate.folder_path,
        useFilterRules: filterOptions.useFilterRules,
        subtitleFilterRules: filterOptions.subtitleFilterRules
      })
      ElMessage.success(data.import_result?.awaiting_manual_match ? '字幕压缩包补配成功，已自动加入工作台' : '字幕压缩包补配成功')
      if (data.task?.id) {
        openImportedTask(data.task.id)
      }
      clearManualArchivePreview()
      return true
    } catch (error) {
      ElMessage.error('执行字幕压缩包补配失败: ' + (error.response?.data?.detail || error.message))
      return false
    } finally {
      window.clearTimeout(fallbackClearTimer)
      manualArchiveImporting.value = false
    }
  }

  return {
    manualArchivePath,
    manualArchiveLoading,
    manualArchiveImporting,
    manualArchivePreview,
    manualCandidateSelection,
    selectedManualCandidate,
    canExecuteManualArchiveImport,

    clearManualArchivePreview,
    previewManualArchive,
    executeManualArchiveImport
  }
}

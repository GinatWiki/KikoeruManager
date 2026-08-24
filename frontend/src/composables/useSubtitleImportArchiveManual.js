import { ref, computed, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { subtitleImportApi, configApi } from '../api'

/**
 * 压缩包补配 - 手动扫描入口。
 *
 * 预检单列表只包含自动处理流程产生的记录；用户手头有字幕压缩包时，
 * 在这里输入路径做预检并直接导入（复用 /subtitle-import/archive/preview|import）。
 *
 * 支持输入文件夹路径：后端会扫描目录内的压缩包——
 * - 目录内只有 1 个压缩包：自动解析为该文件，直接走正常预检
 * - 目录内有多个压缩包：返回目录扫描结果，列出供用户选择后再预检
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
  // 手动扫描目录展开结果：目录内有多个压缩包时列出供选择
  const manualDirectoryArchives = ref([])
  const manualDirectorySource = ref('')

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

  function formatArchiveSize(size) {
    const bytes = Number(size || 0)
    if (!Number.isFinite(bytes) || bytes <= 0) return '-'
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
    return `${(bytes / 1024 / 1024).toFixed(2)} MB`
  }

  function clearManualArchivePreview() {
    manualArchivePreview.value = null
    manualCandidateSelection.value = ''
    manualDirectoryArchives.value = []
    manualDirectorySource.value = ''
  }

  // 用户在目录扫描结果里选中某个压缩包：填入路径并立即重新预检
  async function selectManualDirectoryArchive(archive) {
    const path = String(archive?.path || '').trim()
    if (!path) return
    manualArchivePath.value = path
    manualDirectoryArchives.value = []
    manualDirectorySource.value = ''
    await previewManualArchive()
  }

  async function previewManualArchive() {
    const path = manualArchivePath.value.trim()
    if (!path) {
      ElMessage.warning('请先输入字幕压缩包路径（支持填字幕文件夹路径，将自动扫描目录内压缩包）')
      return
    }

    manualArchiveLoading.value = true
    try {
      const data = await subtitleImportApi.previewArchive(path)
      const preview = data.preview || data

      // 目录扫描结果：多个压缩包待选择，不当普通预检渲染
      if (preview?.is_directory_scan) {
        manualArchivePreview.value = null
        manualDirectoryArchives.value = preview.directory_archives || []
        manualDirectorySource.value = preview.source_path || path
        ElMessage.info(`目录内发现 ${preview.directory_archive_count || manualDirectoryArchives.value.length} 个压缩包，请选择要补配的压缩包`)
        return
      }

      manualDirectoryArchives.value = []
      manualDirectorySource.value = ''
      manualArchivePreview.value = preview

      // 单压缩包目录被后端自动解析：把输入框路径同步为解析后的文件路径，
      // 保证后续"导入"直接走文件路径而不需要再次解析目录
      const resolvedPath = String(preview?.source_path || '').trim()
      if (resolvedPath && preview?.directory_resolved_from && resolvedPath !== path) {
        manualArchivePath.value = resolvedPath
      }

      ElMessage.success('字幕压缩包预检完成')
    } catch (error) {
      manualArchivePreview.value = null
      manualDirectoryArchives.value = []
      manualDirectorySource.value = ''
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
    manualDirectoryArchives,
    manualDirectorySource,

    formatArchiveSize,
    clearManualArchivePreview,
    selectManualDirectoryArchive,
    previewManualArchive,
    executeManualArchiveImport
  }
}

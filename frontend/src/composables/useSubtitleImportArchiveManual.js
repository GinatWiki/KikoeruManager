import { ref, computed, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { subtitleImportApi, configApi } from '../api'

/**
 * 压缩包补配 - 手动扫描入口。
 *
 * 扫描与预检分离：
 * - 「扫描」：轻量清点目录内容（压缩包清单 / 散装字幕计数），不解包、不解析目标，
 *   可随时重复执行；输入框为空时自动回退设置中的默认字幕目录（storage.asmr_subtitle_path）。
 * - 「预检」：对选中的压缩包（或输入框直接指向的压缩包/散装字幕目录）做完整预检，
 *   可能涉及解包探测，由用户显式触发。
 *
 * 输入框始终保持用户填写的"来源目录"不被覆盖；选中要补配的具体条目
 * 记录在 manualSelectedArchive 中，导入时以预检结果的 source_path 为准。
 */
export function useSubtitleImportArchiveManual({
  getSubtitleWorkbenchFilterOptions,
  openImportedTask,
  candidateKey
}) {
  const manualArchivePath = ref('')
  const manualArchiveLoading = ref(false)
  const manualScanLoading = ref(false)
  const manualArchiveImporting = ref(false)
  const manualArchivePreview = ref(null)
  // 目标目录候选支持多选（字幕树↔音频树按相对路径自动路由分发）
  const manualTargetSelections = ref([])
  // 从扫描结果中选中、正在预检/待导入的具体条目路径（不回写输入框）
  const manualSelectedArchive = ref('')
  // 手动扫描目录展开结果：目录内有多个压缩包时列出供选择
  const manualDirectoryArchives = ref([])
  const manualDirectorySource = ref('')
  const manualDirectoryLooseSubtitleCount = ref(0)

  // 设置中的默认字幕目录：输入框为空时扫描自动回退到它；加载失败静默降级
  let defaultSubtitleSourcePath = ''

  void (async () => {
    try {
      const config = await configApi.get()
      defaultSubtitleSourcePath = String(config?.storage?.asmr_subtitle_path || '').trim()
      if (defaultSubtitleSourcePath && !manualArchivePath.value) {
        manualArchivePath.value = defaultSubtitleSourcePath
      }
    } catch (error) {
      console.debug('[subtitle-import] 加载默认字幕目录失败', error)
    }
  })()

  const hasManualScanSource = computed(() =>
    Boolean(manualArchivePath.value.trim() || defaultSubtitleSourcePath)
  )

  const selectedManualCandidates = computed(() => {
    const candidates = manualArchivePreview.value?.candidates || []
    return candidates.filter(
      candidate =>
        candidate?.ready_for_import &&
        manualTargetSelections.value.includes(candidateKey(candidate))
    )
  })

  const canExecuteManualArchiveImport = computed(() => {
    if (!manualArchivePreview.value) return false
    const readyCount = Number(manualArchivePreview.value.ready_candidate_count || 0)
    if (readyCount <= 0) return false
    return selectedManualCandidates.value.length > 0
  })

  watch(manualArchivePreview, (preview) => {
    if (!preview) {
      manualTargetSelections.value = []
      return
    }
    // 默认全选可导入目标；多目标场景下用户可在预检结果中增删勾选
    manualTargetSelections.value = (preview.candidates || [])
      .filter(candidate => candidate?.ready_for_import)
      .map(candidate => candidateKey(candidate))
  }, { immediate: true })

  function toggleManualTargetSelection (candidate) {
    const key = candidateKey(candidate)
    const next = new Set(manualTargetSelections.value)
    if (next.has(key)) {
      next.delete(key)
    } else {
      next.add(key)
    }
    manualTargetSelections.value = [...next]
  }

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
    manualDirectoryLooseSubtitleCount.value = 0
    manualSelectedArchive.value = ''
  }

  function applyDirectoryScanResult(preview, fallbackSource) {
    manualArchivePreview.value = null
    manualTargetSelections.value = []
    manualSelectedArchive.value = ''
    manualDirectoryArchives.value = preview.directory_archives || []
    manualDirectorySource.value = preview.source_path || fallbackSource
    manualDirectoryLooseSubtitleCount.value = Number(preview.loose_subtitle_count || 0)
    const count = manualDirectoryArchives.value.length
    if (count > 0) {
      ElMessage.info(`目录内发现 ${preview.directory_archive_count || count} 个压缩包，请选择要补配的压缩包`)
    } else if (manualDirectoryLooseSubtitleCount.value > 0) {
      ElMessage.info(`目录内无压缩包，发现 ${manualDirectoryLooseSubtitleCount.value} 个散装字幕文件，可直接预检按文件夹补配处理`)
    } else {
      ElMessage.warning('目录内未发现压缩包或字幕文件')
    }
  }

  // 「扫描」：轻量清点来源目录内容。输入框为空时回退设置中的默认字幕目录。
  async function scanManualDirectory() {
    if (manualScanLoading.value || manualArchiveLoading.value) return
    let source = manualArchivePath.value.trim()
    if (!source) {
      source = defaultSubtitleSourcePath
      if (source) {
        manualArchivePath.value = source
        ElMessage.info(`输入为空，已使用设置中的默认字幕目录: ${source}`)
      }
    }
    if (!source) {
      ElMessage.warning('请先输入字幕来源路径，或在设置中配置 ASMR 字幕目录')
      return
    }

    manualScanLoading.value = true
    try {
      const data = await subtitleImportApi.previewArchive(source, { scanOnly: true })
      const preview = data.preview || data
      applyDirectoryScanResult(preview, source)
    } catch (error) {
      manualDirectoryArchives.value = []
      manualDirectorySource.value = ''
      manualDirectoryLooseSubtitleCount.value = 0
      ElMessage.error('扫描失败: ' + (error.response?.data?.detail || error.message))
    } finally {
      manualScanLoading.value = false
    }
  }

  // 用户在扫描结果里选中某个压缩包：只记录选中项，不改写输入框，随后立即预检
  async function selectManualDirectoryArchive(archive) {
    const path = String(archive?.path || '').trim()
    if (!path) return
    manualSelectedArchive.value = path
    await previewManualArchive()
  }

  // 预检目标优先级：扫描结果选中项 > 输入框路径（空则回退默认目录）
  function resolveManualPreviewTarget() {
    return (
      manualSelectedArchive.value.trim() ||
      manualArchivePath.value.trim() ||
      defaultSubtitleSourcePath
    )
  }

  async function previewManualArchive() {
    const path = resolveManualPreviewTarget()
    if (!path) {
      ElMessage.warning('请先选择要预检的压缩包，或输入字幕压缩包/字幕文件夹路径')
      return
    }

    manualArchiveLoading.value = true
    try {
      const data = await subtitleImportApi.previewArchive(path)
      const preview = data.preview || data

      // 目录清单结果：列出供选择，不当普通预检渲染
      if (preview?.is_directory_scan) {
        applyDirectoryScanResult(preview, path)
        return
      }

      manualDirectoryArchives.value = []
      manualDirectorySource.value = ''
      manualDirectoryLooseSubtitleCount.value = 0
      manualArchivePreview.value = preview

      // 后端把目录自动解析成单个压缩包/散装字幕目录时，
      // 把解析结果记入选中项（不覆盖用户输入框），保证"导入"直接用解析后的路径
      const resolvedPath = String(preview?.source_path || '').trim()
      if (preview?.directory_resolved_from && resolvedPath && resolvedPath !== path) {
        manualSelectedArchive.value = resolvedPath
      }

      ElMessage.success(
        preview?.mode === 'subtitle_folder'
          ? '字幕文件夹预检完成（目录内无压缩包，已按散装字幕处理）'
          : '字幕压缩包预检完成'
      )
    } catch (error) {
      manualArchivePreview.value = null
      ElMessage.error('字幕压缩包预检失败: ' + (error.response?.data?.detail || error.message))
    } finally {
      manualArchiveLoading.value = false
    }
  }

  async function executeManualArchiveImport() {
    const candidates = selectedManualCandidates.value
    if (!candidates.length) return false

    // 导入一律使用预检确认过的实际来源路径（压缩包文件或散装字幕目录）
    const path = String(manualArchivePreview.value?.source_path || '').trim() || resolveManualPreviewTarget()
    if (!path) return false

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
      // 散装字幕目录（preview.mode === 'subtitle_folder'）走 folder 导入 API
      const isFolderMode = manualArchivePreview.value?.mode === 'subtitle_folder'
      // 多目标：把勾选的全部目标交给后端做字幕树↔音频树路由分发
      const requestOptions = {
        targetLibraryId: candidates[0].library_id,
        targetFolderPath: candidates[0].folder_path,
        targetFolders: candidates.map(candidate => ({
          library_id: candidate.library_id,
          folder_path: candidate.folder_path
        })),
        useFilterRules: filterOptions.useFilterRules,
        subtitleFilterRules: filterOptions.subtitleFilterRules
      }
      const data = isFolderMode
        ? await subtitleImportApi.importFolder(path, requestOptions)
        : await subtitleImportApi.importArchive(path, requestOptions)
      const targetCount = Number(data.import_result?.target_count || 0)
      ElMessage.success(
        data.import_result?.awaiting_manual_match
          ? (targetCount > 1 ? `字幕补配成功，已分发到 ${targetCount} 个目标并加入工作台` : '字幕补配成功，已自动加入工作台')
          : '字幕补配成功'
      )
      const firstTaskId = data.task?.id || data.tasks?.[0]?.id
      if (firstTaskId) {
        openImportedTask(firstTaskId)
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
    manualScanLoading,
    manualArchiveImporting,
    manualArchivePreview,
    manualTargetSelections,
    selectedManualCandidates,
    canExecuteManualArchiveImport,
    manualDirectoryArchives,
    manualDirectorySource,
    manualDirectoryLooseSubtitleCount,
    manualSelectedArchive,
    hasManualScanSource,

    formatArchiveSize,
    clearManualArchivePreview,
    selectManualDirectoryArchive,
    toggleManualTargetSelection,
    scanManualDirectory,
    previewManualArchive,
    executeManualArchiveImport
  }
}

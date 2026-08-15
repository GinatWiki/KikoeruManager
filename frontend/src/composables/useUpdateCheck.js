import { ref } from 'vue'
import { healthApi } from '../api'

// 应用常驻时每 6 小时静默复查一次（桌面端可能连续运行数天）
const UPDATE_CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000

/**
 * 版本更新检测：向后端 /version/update-check 询问 GitHub 最新 Release，
 * 发现新版本时返回高亮与跳转所需的信息。检查失败静默降级，不影响业务。
 */
export function useUpdateCheck() {
  // 有新版本时：{ latestVersion, latestTag, releaseUrl }，否则为 null
  const updateInfo = ref(null)
  const updateChecking = ref(false)
  let intervalHandle = null

  async function checkUpdate() {
    if (updateChecking.value) return
    updateChecking.value = true
    try {
      const data = await healthApi.checkUpdate()
      if (data?.success && data?.has_update) {
        updateInfo.value = {
          latestVersion: data.latest_version,
          latestTag: data.latest_tag,
          releaseUrl: data.release_url,
        }
      } else {
        updateInfo.value = null
      }
    } catch (error) {
      console.warn('[更新检测] 检查失败，已静默忽略', error)
      updateInfo.value = null
    } finally {
      updateChecking.value = false
    }
  }

  function openUpdateRelease() {
    const url = updateInfo.value?.releaseUrl
    if (!url) return
    window.open(url, '_blank', 'noopener')
  }

  function startUpdateCheck() {
    checkUpdate()
    if (intervalHandle) clearInterval(intervalHandle)
    intervalHandle = setInterval(checkUpdate, UPDATE_CHECK_INTERVAL_MS)
  }

  function stopUpdateCheck() {
    if (intervalHandle) {
      clearInterval(intervalHandle)
      intervalHandle = null
    }
  }

  return {
    updateInfo,
    updateChecking,
    checkUpdate,
    openUpdateRelease,
    startUpdateCheck,
    stopUpdateCheck,
  }
}

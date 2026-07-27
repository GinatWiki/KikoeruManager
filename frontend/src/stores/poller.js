import { onUnmounted, getCurrentInstance } from 'vue'

/**
 * 全局统一轮询调度器
 * 替代各页面分散的 setInterval：
 * - 单一全局定时器（基准 3 秒），多个页面按需订阅
 * - 引用计数：没有订阅者时自动停止定时器
 * - 浏览器标签页隐藏时自动暂停（document.hidden）
 * - 组件卸载时自动注销（在 setup 中调用时）
 */

const BASE_INTERVAL = 3000

const subscribers = new Map() // id -> { callback, every }
let timerId = null
let tickCount = 0

function tick() {
  tickCount++
  for (const [id, sub] of subscribers) {
    if (tickCount % sub.every !== 0) continue
    Promise.resolve()
      .then(() => sub.callback())
      .catch(err => console.error(`[poller] 订阅 ${id} 执行失败:`, err))
  }
}

function ensureTimer() {
  if (timerId) return
  timerId = setInterval(() => {
    // 标签页隐藏时暂停轮询，节省请求
    if (typeof document !== 'undefined' && document.hidden) return
    tick()
  }, BASE_INTERVAL)
}

function stopTimerIfIdle() {
  if (subscribers.size === 0 && timerId) {
    clearInterval(timerId)
    timerId = null
  }
}

/**
 * 订阅轮询
 * @param {string} id 唯一标识（同一 id 重复订阅会覆盖）
 * @param {Function} callback 每次轮询执行的函数（可以是 async）
 * @param {number} every 每几个基准周期执行一次（1=3秒, 2=6秒...）
 * @returns {{ unsubscribe: Function, trigger: Function }}
 */
export function usePoller(id, callback, every = 1) {
  subscribers.set(id, { callback, every })
  ensureTimer()

  const unsubscribe = () => {
    subscribers.delete(id)
    stopTimerIfIdle()
  }

  // 在组件 setup 中调用时，卸载自动注销
  if (getCurrentInstance()) {
    onUnmounted(unsubscribe)
  }

  return {
    unsubscribe,
    trigger: () => Promise.resolve().then(() => callback())
  }
}

/** 手动注销（非 setup 场景） */
export function removePoller(id) {
  subscribers.delete(id)
  stopTimerIfIdle()
}

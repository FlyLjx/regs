const { createApp, reactive, ref, computed, onMounted, onBeforeUnmount } = Vue

async function readResponsePayload(response) {
  const text = await response.text()
  if (!text) return null
  const contentType = String(response.headers.get("content-type") || "")
  if (contentType.includes("application/json")) {
    try {
      return JSON.parse(text)
    } catch {
      return { raw: text }
    }
  }
  try {
    return JSON.parse(text)
  } catch {
    return { message: text }
  }
}

function extractErrorMessage(payload, fallback = "请求失败") {
  return payload?.detail?.error || payload?.error || payload?.message || payload?.raw || fallback
}

async function apiRequest(url, options = {}) {
  const response = await fetch(url, options)
  const payload = await readResponsePayload(response)
  if (!response.ok) {
    throw new Error(extractErrorMessage(payload, response.statusText || "请求失败"))
  }
  return payload
}

createApp({
  setup() {
    const loading = ref(true)
    const saving = ref(false)
    const actionLoading = ref(false)
    const proxyTesting = ref(false)
    const cloudLoading = ref(false)
    const pageTab = ref("register")
    const settingsTab = ref("basic")
    const logCursor = ref(0)
    const pollTimer = ref(null)
    const runtimeRefreshing = ref(false)
    const cloudRefreshing = ref(false)

    const settings = reactive({
      count: 20,
      threads: 3,
      proxy: "",
      enable_warp_registration: false,
      server: "",
      auth_key: "",
      min_active_accounts: 60,
      monitor_interval_seconds: 300,
      upload_to_cloud: true,
      enable_flaresolverr: false,
      flaresolverr_url: "",
      flaresolverr_preload: false,
      flaresolverr_max_solve_attempts: 1,
    })

    const editor = reactive({
      register_config_text: "",
      env_text: "",
    })

    const runtime = reactive({
      busy: false,
      monitoring: false,
      status: "idle",
      current_task: "",
      hint: "",
      monitor_countdown_text: "未启动",
      progress: {
        total: 0,
        submitted: 0,
        done: 0,
        success: 0,
        fail: 0,
        running: 0,
      },
    })

    const cloud = reactive({
      valid_account_count: 0,
      healthy: false,
      status: "",
      summary: {},
    })

    const logs = ref([])

    const statusLabel = computed(() => {
      if (runtime.monitoring) return "监控中"
      if (runtime.busy) return "执行中"
      return "空闲"
    })

    const statusType = computed(() => {
      if (runtime.monitoring) return "success"
      if (runtime.busy) return "warning"
      return "info"
    })

    const progressPercent = computed(() => {
      if (!runtime.progress.total) return 0
      return Math.min(100, Math.round((runtime.progress.done / runtime.progress.total) * 100))
    })

    const progressRows = computed(() => [
      { label: "总数", value: String(runtime.progress.total) },
      { label: "已完成", value: `${runtime.progress.done} / ${runtime.progress.total}` },
      { label: "成功", value: String(runtime.progress.success) },
      { label: "失败", value: String(runtime.progress.fail) },
      { label: "运行中", value: String(runtime.progress.running) },
    ])

    const displayLogs = computed(() => [...logs.value].reverse())

    const stats = computed(() => [
      { label: "当前状态", value: statusLabel.value, extra: runtime.hint || "等待操作" },
      { label: "注册规模", value: `${settings.count} / ${settings.threads}`, extra: "数量 / 线程" },
      { label: "代理", value: settings.proxy || "直连", extra: "统一代理", kind: "proxy" },
      { label: "云端账号", value: String(cloud.valid_account_count || 0), extra: cloud.status || "未读取" },
    ])

    function metricValueClass(item) {
      return item?.kind ? ["metric-value", `metric-value-${item.kind}`] : ["metric-value"]
    }

    function patchObject(target, payload) {
      Object.keys(target).forEach((key) => {
        if (Object.prototype.hasOwnProperty.call(payload || {}, key)) {
          target[key] = payload[key]
        }
      })
    }

    function appendLogs(items) {
      if (!Array.isArray(items) || !items.length) return
      logs.value.push(...items)
      if (logs.value.length > 500) {
        logs.value.splice(0, logs.value.length - 500)
      }
    }

    async function bootstrap() {
      loading.value = true
      try {
        const data = await apiRequest("/api/bootstrap")
        patchObject(settings, data.settings || {})
        editor.register_config_text = data.register_config_text || ""
        editor.env_text = data.env_text || ""
        patchObject(runtime, data.runtime || {})
        runtime.progress = data.runtime?.progress || runtime.progress
        logs.value = data.logs?.items || []
        logCursor.value = data.logs?.cursor || 0
      } finally {
        loading.value = false
      }
    }

    async function refreshRuntime() {
      if (runtimeRefreshing.value) return
      runtimeRefreshing.value = true
      try {
        const [runtimeData, logData] = await Promise.all([
          apiRequest("/api/runtime"),
          apiRequest(`/api/logs?cursor=${logCursor.value}`),
        ])
        patchObject(runtime, runtimeData || {})
        runtime.progress = runtimeData?.progress || runtime.progress
        appendLogs(logData?.items || [])
        logCursor.value = logData?.cursor || logCursor.value
      } finally {
        runtimeRefreshing.value = false
      }
    }

    async function refreshCloud(options = {}) {
      const silent = !!options.silent
      if (cloudRefreshing.value) return
      cloudRefreshing.value = true
      if (!silent) {
        cloudLoading.value = true
      }
      try {
        const data = await apiRequest("/api/cloud-summary")
        cloud.valid_account_count = data.valid_account_count || 0
        cloud.healthy = !!data.healthy
        cloud.status = data.status || ""
        cloud.summary = data.summary || {}
        if (!silent) {
          ElementPlus.ElMessage.success(`云端有效账号数：${cloud.valid_account_count}`)
        }
      } catch (error) {
        if (!silent) {
          ElementPlus.ElMessage.error(error.message)
        }
      } finally {
        cloudRefreshing.value = false
        if (!silent) {
          cloudLoading.value = false
        }
      }
    }

    async function saveAllSettings() {
      saving.value = true
      try {
        const data = await apiRequest("/api/settings", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            settings: { ...settings },
            register_config_text: editor.register_config_text,
            env_text: editor.env_text,
          }),
        })
        patchObject(settings, data.settings || {})
        editor.register_config_text = data.register_config_text || editor.register_config_text
        editor.env_text = data.env_text || editor.env_text
        ElementPlus.ElMessage.success("配置已保存")
      } catch (error) {
        ElementPlus.ElMessage.error(error.message)
      } finally {
        saving.value = false
      }
    }

    async function testProxy() {
      const proxyValue = String(settings.proxy || "").trim()
      if (!proxyValue) {
        ElementPlus.ElMessage.warning("请先填写代理地址")
        return
      }
      ElementPlus.ElMessage.info("开始测试代理连接，请稍候")
      proxyTesting.value = true
      try {
        const result = await apiRequest("/api/actions/proxy/test", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ proxy: proxyValue }),
        })
        const items = Array.isArray(result?.results) ? result.results : []
        const summary = items
          .map((item) => `${item.target}:${item.reachable ? "可用" : "失败"}${item.status_code ? `(${item.status_code})` : ""}`)
          .join("，")
        ElementPlus.ElMessage.success(summary || result?.message || "代理测试完成")
        await refreshRuntime()
      } catch (error) {
        ElementPlus.ElMessage.error(error.message)
      } finally {
        proxyTesting.value = false
      }
    }

    async function executeAction(url, successText) {
      actionLoading.value = true
      try {
        await apiRequest(url, { method: "POST" })
        ElementPlus.ElMessage.success(successText)
        await refreshRuntime()
      } catch (error) {
        ElementPlus.ElMessage.error(error.message)
      } finally {
        actionLoading.value = false
      }
    }

    async function toggleMonitor() {
      if (runtime.monitoring) {
        await executeAction("/api/actions/monitor/stop", "已请求停止监控")
      } else {
        await executeAction("/api/actions/monitor/start", "监控已启动")
      }
    }

    function levelTagType(level) {
      if (level === "error") return "danger"
      if (level === "success") return "success"
      if (level === "warning") return "warning"
      if (level === "debug") return "info"
      return ""
    }

    function startPolling() {
      stopPolling()
      pollTimer.value = window.setInterval(() => {
        refreshRuntime().catch(() => {})
        refreshCloud({ silent: true }).catch(() => {})
      }, 2000)
    }

    function stopPolling() {
      if (pollTimer.value) {
        window.clearInterval(pollTimer.value)
        pollTimer.value = null
      }
    }

    onMounted(async () => {
      try {
        await bootstrap()
        await refreshCloud({ silent: true })
        startPolling()
      } catch (error) {
        ElementPlus.ElMessage.error(error.message)
      }
    })

    onBeforeUnmount(() => {
      stopPolling()
    })

    return {
      loading,
      saving,
      actionLoading,
      proxyTesting,
      cloudLoading,
      pageTab,
      settingsTab,
      settings,
      editor,
      runtime,
      cloud,
      logs,
      displayLogs,
      statusLabel,
      statusType,
      progressPercent,
      progressRows,
      stats,
      metricValueClass,
      saveAllSettings,
      testProxy,
      executeAction,
      toggleMonitor,
      refreshRuntime,
      refreshCloud,
      levelTagType,
    }
  },
  template: `
    <div class="app-shell" v-loading="loading">
      <div class="hero">
        <div>
          <div class="eyebrow">ChatGPT2API 注册控制台</div>
          <h1>中文 Web 管理页</h1>
          <p>支持本地注册、检查补号、日志查看和自定义代理。</p>
        </div>
        <div class="hero-badge">
          <el-tag :type="statusType" size="large">{{ statusLabel }}</el-tag>
          <div class="hero-muted">{{ runtime.monitor_countdown_text || '未启动' }}</div>
        </div>
      </div>

      <el-row :gutter="16" class="stats-grid">
        <el-col v-for="item in stats" :key="item.label" :xs="24" :sm="12" :lg="6">
          <el-card shadow="never" class="metric-card">
            <div class="metric-label">{{ item.label }}</div>
            <div :class="metricValueClass(item)">{{ item.value }}</div>
            <div class="metric-extra">{{ item.extra }}</div>
          </el-card>
        </el-col>
      </el-row>

      <el-card shadow="never" class="panel-card">
        <el-tabs v-model="pageTab">
          <el-tab-pane label="注册" name="register">
            <el-row :gutter="16">
              <el-col :xs="24" :lg="14">
                <el-card shadow="never" class="inner-card">
                  <template #header>
                    <div class="card-header">
                      <div>
                        <div class="card-title">注册操作</div>
                        <div class="card-subtitle">开始注册、补号和监控都在这里。</div>
                      </div>
                      <el-tag type="info">{{ progressPercent }}%</el-tag>
                    </div>
                  </template>

                  <el-space wrap>
                    <el-button type="primary" :loading="actionLoading" @click="executeAction('/api/actions/register', '注册任务已启动')">开始注册</el-button>
                    <el-button :loading="actionLoading" @click="executeAction('/api/actions/refill', '补号任务已启动')">检查补号</el-button>
                    <el-button :type="runtime.monitoring ? 'danger' : 'success'" plain :loading="actionLoading" @click="toggleMonitor">
                      {{ runtime.monitoring ? '停止监控' : '开启监控' }}
                    </el-button>
                    <el-button plain :loading="cloudLoading" @click="refreshCloud">读取云端</el-button>
                    <a href="/api/logs/export" target="_blank" rel="noreferrer" class="plain-link">
                      <el-button plain>导出日志</el-button>
                    </a>
                  </el-space>
                  <el-divider />

                  <el-progress :percentage="progressPercent" :stroke-width="14" />
                  <el-descriptions :column="1" border class="detail-table">
                    <el-descriptions-item v-for="row in progressRows" :key="row.label" :label="row.label">
                      {{ row.value }}
                    </el-descriptions-item>
                  </el-descriptions>
                </el-card>
              </el-col>

              <el-col :xs="24" :lg="10">
                <div class="side-stack">
                  <el-card shadow="never" class="inner-card">
                    <template #header>
                      <div class="card-header">
                        <div>
                          <div class="card-title">云端概览</div>
                          <div class="card-subtitle">当前云端账号统计与健康状态。</div>
                        </div>
                        <el-button size="small" plain :loading="cloudLoading" @click="refreshCloud">刷新</el-button>
                      </div>
                    </template>
                    <el-descriptions :column="1" border class="detail-table cloud-summary-table">
                      <el-descriptions-item label="有效账号数">{{ cloud.valid_account_count }}</el-descriptions-item>
                      <el-descriptions-item label="状态">{{ cloud.status || '未读取' }}</el-descriptions-item>
                      <el-descriptions-item label="健康状态">{{ cloud.healthy ? '正常' : '未确认' }}</el-descriptions-item>
                    </el-descriptions>
                  </el-card>

                  <el-card shadow="never" class="inner-card">
                    <template #header>
                      <div class="card-header">
                        <div>
                          <div class="card-title">实时日志</div>
                          <div class="card-subtitle">操作过程和错误提示会持续追加。</div>
                        </div>
                        <el-button size="small" plain @click="refreshRuntime">刷新日志</el-button>
                      </div>
                    </template>
                    <el-table :data="displayLogs" stripe border max-height="420" empty-text="暂无日志">
                      <el-table-column prop="timestamp" label="时间" width="180" />
                      <el-table-column label="级别" width="100">
                        <template #default="scope">
                          <el-tag :type="levelTagType(scope.row.level)" effect="plain">
                            {{ scope.row.level || 'info' }}
                          </el-tag>
                        </template>
                      </el-table-column>
                      <el-table-column prop="message" label="消息" min-width="420" show-overflow-tooltip />
                    </el-table>
                  </el-card>
                </div>
              </el-col>
            </el-row>
          </el-tab-pane>

          <el-tab-pane label="设置" name="settings">
            <div class="settings-header">
              <div>
                <div class="card-title">配置中心</div>
                <div class="card-subtitle">这里管理代理、云端地址、补号阈值、register.json 和 .env。</div>
              </div>
              <el-button type="primary" :loading="saving" @click="saveAllSettings">保存配置</el-button>
            </div>

            <el-tabs v-model="settingsTab" class="settings-tabs">
              <el-tab-pane label="基础设置" name="basic">
                <el-form label-position="top" class="form-grid">
                  <el-row :gutter="16">
                    <el-col :xs="24" :md="12">
                      <el-form-item label="自定义代理">
                        <el-input v-model="settings.proxy" placeholder="http://127.0.0.1:7890" clearable />
                      </el-form-item>
                    </el-col>
                    <el-col :xs="24" :md="12">
                      <el-form-item label="注册数量">
                        <el-input-number v-model="settings.count" :min="1" :max="9999" controls-position="right" class="w-full" />
                      </el-form-item>
                    </el-col>
                    <el-col :xs="24" :md="12">
                      <el-form-item label="并发线程">
                        <el-input-number v-model="settings.threads" :min="1" :max="128" controls-position="right" class="w-full" />
                      </el-form-item>
                    </el-col>
                    <el-col :xs="24" :md="12">
                      <el-form-item label="云端地址">
                        <el-input v-model="settings.server" placeholder="https://your-server.example" clearable />
                      </el-form-item>
                    </el-col>
                    <el-col :xs="24" :md="12">
                      <el-form-item label="管理员密钥">
                        <el-input v-model="settings.auth_key" show-password clearable />
                      </el-form-item>
                    </el-col>
                    <el-col :xs="24" :md="12">
                      <el-form-item label="最小有效账号">
                        <el-input-number v-model="settings.min_active_accounts" :min="1" :max="99999" controls-position="right" class="w-full" />
                      </el-form-item>
                    </el-col>
                    <el-col :xs="24" :md="12">
                      <el-form-item label="监控间隔（秒）">
                        <el-input-number v-model="settings.monitor_interval_seconds" :min="5" :max="86400" controls-position="right" class="w-full" />
                      </el-form-item>
                    </el-col>
                    <el-col :xs="24" :md="12">
                      <el-form-item label="FlareSolverr URL">
                        <el-input v-model="settings.flaresolverr_url" placeholder="http://flaresolverr:8191" clearable />
                      </el-form-item>
                    </el-col>
                    <el-col :xs="24" :md="12">
                      <el-form-item label="单任务最大求解次数">
                        <el-input-number v-model="settings.flaresolverr_max_solve_attempts" :min="1" :max="10" controls-position="right" class="w-full" />
                      </el-form-item>
                    </el-col>
                  </el-row>

                  <el-space wrap>
                    <el-switch v-model="settings.upload_to_cloud" inline-prompt active-text="默认上传云端" inactive-text="仅本地模式" />
                    <el-switch v-model="settings.enable_warp_registration" inline-prompt active-text="WARP 注册开" inactive-text="WARP 注册关" />
                    <el-switch v-model="settings.enable_flaresolverr" inline-prompt active-text="FlareSolverr 开" inactive-text="FlareSolverr 关" />
                    <el-switch v-model="settings.flaresolverr_preload" inline-prompt active-text="预热求解开" inactive-text="按需求解" />
                    <el-button :loading="proxyTesting" @click="testProxy">测试代理</el-button>
                  </el-space>
                </el-form>
              </el-tab-pane>

              <el-tab-pane label="register.json" name="register">
                <el-input v-model="editor.register_config_text" type="textarea" :rows="18" resize="vertical" />
              </el-tab-pane>

              <el-tab-pane label=".env" name="env">
                <el-input v-model="editor.env_text" type="textarea" :rows="18" resize="vertical" />
              </el-tab-pane>
            </el-tabs>
          </el-tab-pane>
        </el-tabs>
      </el-card>
    </div>
  `,
}).use(ElementPlus).mount("#app")

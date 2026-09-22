import { type ElementType, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  Ban,
  Calculator,
  CheckCircle2,
  CircleDashed,
  Clock3,
  Database,
  Gauge,
  KeyRound,
  Layers3,
  LoaderCircle,
  Network,
  RefreshCw,
  Route,
  ShieldAlert,
  ShieldBan,
  ShieldCheck,
  Timer,
  Undo2,
  XCircle,
  Zap,
} from 'lucide-react'
import { api, type RuntimeSettings } from '@/lib/api'
import { StatusBadge } from '@/lib/status'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { IconBadge, type IconBadgeTone } from '@/components/ui/icon-badge'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ActionToolbar, ToolbarAction } from '@/components/action-toolbar'
import { EnabledBadge } from '@/components/enabled-badge'
import { MonitorStatusBadge } from '@/components/monitor-status-badge'
import { Page, PageHeader } from '@/components/page'
import { TitledCard } from '@/components/titled-card'
import { autoIsolationMinStatusLabel } from '../components/settings-model'

type Thresholds = Pick<
  RuntimeSettings,
  | 'analysisWindowHours'
  | 'degradationTps'
  | 'strongDegradationTps'
  | 'consecutiveAnomalies'
  | 'cumulativeAnomalyRate'
  | 'highRiskHardCount'
  | 'riskAnomalyRateWeight'
  | 'riskHardWeight'
  | 'riskHardCap'
  | 'riskFastWeight'
  | 'riskFastCap'
  | 'riskMarkerMissWeight'
  | 'riskMarkerMissCap'
  | 'riskStreakWeight'
  | 'riskStreakCap'
  | 'riskScoreCap'
  | 'riskWatchFloor'
  | 'riskSuspectFloor'
  | 'riskHighFloor'
  | 'bufferFirstTokenShare'
  | 'minGenerationMs'
  | 'minimumOutputTokens'
  | 'reasoningZeroRiskEnabled'
  | 'autoQuarantine'
  | 'autoIsolationEnabled'
  | 'autoIsolationMinStatus'
  | 'quarantineMinutes'
>

type RuleCardProps = {
  icon: ElementType
  title: string
  badge?: ReactNode
  summary: string
  conditions?: string[]
  tone?: 'default' | 'success' | 'warning' | 'danger' | 'info'
}

const defaultThresholds: Thresholds = {
  analysisWindowHours: 168,
  degradationTps: 150,
  strongDegradationTps: 500,
  consecutiveAnomalies: 3,
  cumulativeAnomalyRate: 0.5,
  highRiskHardCount: 2,
  riskAnomalyRateWeight: 30,
  riskHardWeight: 6,
  riskHardCap: 24,
  riskFastWeight: 12,
  riskFastCap: 30,
  riskMarkerMissWeight: 16,
  riskMarkerMissCap: 32,
  riskStreakWeight: 3,
  riskStreakCap: 15,
  riskScoreCap: 100,
  riskWatchFloor: 15,
  riskSuspectFloor: 50,
  riskHighFloor: 75,
  bufferFirstTokenShare: 0.85,
  minGenerationMs: 250,
  minimumOutputTokens: 32,
  reasoningZeroRiskEnabled: true,
  autoQuarantine: false,
  autoIsolationEnabled: false,
  autoIsolationMinStatus: 'high_risk',
  quarantineMinutes: 30,
}

const ruleIconTone: Record<
  NonNullable<RuleCardProps['tone']>,
  IconBadgeTone
> = {
  default: 'muted',
  success: 'success',
  warning: 'warning',
  danger: 'destructive',
  info: 'info',
}

export function DecisionGuidePage() {
  const settings = useQuery({
    queryKey: ['settings', 'decision-guide'],
    queryFn: api.settings,
    retry: false,
  })
  const thresholds = settings.data ?? defaultThresholds

  return (
    <Page>
      <PageHeader
        title='判定说明'
        description='探针样本如何分类、风险分如何累计，以及账号、任务和恢复状态如何流转。'
        descriptionAsHint
        actions={
          <ActionToolbar label='判定说明操作'>
            <Badge
              variant={settings.data ? 'success' : 'secondary'}
              className='h-8 border-0 bg-background px-2.5'
            >
              <Database />
              {settings.data ? '当前运行配置' : '默认阈值示例'}
            </Badge>
            <ToolbarAction
              label='刷新运行阈值'
              pending={settings.isFetching}
              onClick={() => void settings.refetch()}
            >
              <RefreshCw />
            </ToolbarAction>
          </ActionToolbar>
        }
      />

      <div className='grid gap-2 md:grid-cols-[1fr_auto_1fr_auto_1fr_auto_1fr] md:items-center'>
        <FlowStep
          icon={Activity}
          tone='primary'
          title='发起探针'
          description='固定账号并请求上游'
        />
        <FlowArrow />
        <FlowStep
          icon={Network}
          tone='sky'
          title='核验审计'
          description='确认实际账号与出口'
        />
        <FlowArrow />
        <FlowStep
          icon={Gauge}
          tone='amber'
          title='分类样本'
          description='计算 TPS 与缓冲特征'
        />
        <FlowArrow />
        <FlowStep
          icon={ShieldCheck}
          tone='emerald'
          title='聚合账号'
          description='生成风险分与状态'
        />
      </div>

      <Tabs defaultValue='account' className='space-y-4'>
        <TabsList className='grid w-full grid-cols-2 rounded-md lg:inline-grid lg:w-fit lg:grid-cols-4'>
          <TabsTrigger value='account'>
            <ShieldCheck />
            账号判定
          </TabsTrigger>
          <TabsTrigger value='sample'>
            <Gauge />
            样本分类
          </TabsTrigger>
          <TabsTrigger value='task'>
            <Activity />
            任务与恢复
          </TabsTrigger>
          <TabsTrigger value='upstream'>
            <KeyRound />
            上游字段
          </TabsTrigger>
        </TabsList>

        <TabsContent value='account' className='space-y-4'>
          <ThresholdOverview thresholds={thresholds} />
          <AccountStatusRules thresholds={thresholds} />
          <RiskFormula thresholds={thresholds} />
        </TabsContent>

        <TabsContent value='sample' className='space-y-4'>
          <SampleMetricRules thresholds={thresholds} />
          <SampleClassificationRules thresholds={thresholds} />
        </TabsContent>

        <TabsContent value='task' className='space-y-4'>
          <TaskStatusRules />
          <RestoreStatusRules />
        </TabsContent>

        <TabsContent value='upstream' className='space-y-4'>
          <UpstreamStatusRules />
        </TabsContent>
      </Tabs>
    </Page>
  )
}

function ThresholdOverview({ thresholds }: { thresholds: Thresholds }) {
  const values = [
    {
      icon: Clock3,
      label: '分析窗口',
      value: `${thresholds.analysisWindowHours} 小时`,
    },
    {
      icon: Gauge,
      label: '降智 / 强降智 TPS',
      value: `${thresholds.degradationTps} / ${thresholds.strongDegradationTps}`,
    },
    {
      icon: Layers3,
      label: '重复信号',
      value: `${thresholds.consecutiveAnomalies} 次`,
    },
    {
      icon: Calculator,
      label: '累计异常占比',
      value: formatPercent(thresholds.cumulativeAnomalyRate),
    },
    {
      icon: ShieldAlert,
      label: '高风险强信号',
      value: `至少 ${thresholds.highRiskHardCount} 次`,
    },
    {
      icon: Timer,
      label: '缓冲比例',
      value: `${formatPercent(thresholds.bufferFirstTokenShare)}`,
    },
  ]

  return (
    <TitledCard
      icon={<Gauge />}
      iconTone='primary'
      title='当前判定阈值'
      description='数值来自系统设置；服务未连接时展示后端默认值。'
      contentClassName='p-0 sm:p-0'
    >
      <div className='grid grid-cols-2 gap-px bg-border md:grid-cols-3 xl:grid-cols-6'>
        {values.map(({ icon: Icon, label, value }) => (
          <div key={label} className='bg-background px-4 py-3.5'>
            <div className='flex items-center gap-1.5 text-xs text-muted-foreground'>
              <Icon className='size-3.5' />
              {label}
            </div>
            <div className='mt-2 text-xl font-semibold tracking-tight tabular-nums'>
              {value}
            </div>
          </div>
        ))}
      </div>
    </TitledCard>
  )
}

function AccountStatusRules({ thresholds }: { thresholds: Thresholds }) {
  const anomalyRate = formatPercent(thresholds.cumulativeAnomalyRate)
  const repeated = `风险周期内最近连续信号达到 ${thresholds.consecutiveAnomalies} 次，或累计至少 ${thresholds.consecutiveAnomalies} 次且占可测样本 ${anomalyRate} 以上；复测正常后连续条件会退出，请求错误、无法测量和样本不足不计入可测样本`

  return (
    <TitledCard
      icon={<ShieldCheck />}
      iconTone='emerald'
      title='账号监控状态'
      description='聚合风险周期内账号当前固定出口、临时切换出口及上游调度诊断产生的有效样本。'
    >
      <div className='grid gap-3 lg:grid-cols-2 xl:grid-cols-3'>
        <RuleCard
          icon={CheckCircle2}
          title='正常'
          badge={<MonitorStatusBadge status='healthy' />}
          summary='窗口内没有记录到降智信号。'
          conditions={['降智信号数为 0', '风险分通常为 0']}
          tone='success'
        />
        <RuleCard
          icon={AlertTriangle}
          title='观察'
          badge={<MonitorStatusBadge status='watch' />}
          summary='已经记录到降智信号，但次数尚未达到疑似降智条件。'
          conditions={[
            '降智信号数大于 0',
            `未连续达到 ${thresholds.consecutiveAnomalies} 次，且累计信号未同时满足次数和 ${anomalyRate} 占比`,
          ]}
          tone='warning'
        />
        <RuleCard
          icon={ShieldAlert}
          title='疑似降智'
          badge={<MonitorStatusBadge status='suspect' />}
          summary='风险周期内降智信号已经重复出现，但强证据还不足。'
          conditions={[
            repeated,
            `强降智信号少于 ${thresholds.highRiskHardCount} 次`,
          ]}
          tone='danger'
        />
        <RuleCard
          icon={Zap}
          title='高风险'
          badge={<MonitorStatusBadge status='high_risk' />}
          summary={`风险周期内重复异常，并且已经出现至少 ${thresholds.highRiskHardCount} 次强降智信号。`}
          conditions={[
            repeated,
            `强降智信号至少 ${thresholds.highRiskHardCount} 次`,
          ]}
          tone='danger'
        />
        <RuleCard
          icon={Ban}
          title='已停用'
          badge={<MonitorStatusBadge status='quarantined' />}
          summary='由人工暂时停用，或高风险命中到期停用策略后进入；到期后可自动恢复。'
          conditions={[
            thresholds.autoQuarantine
              ? `当前已启用到期停用，默认 ${thresholds.quarantineMinutes} 分钟`
              : '当前未启用到期停用，只会由人工暂时停用产生',
            '隔离有效期内会覆盖重新计算出的普通监控状态',
          ]}
          tone='danger'
        />
        <RuleCard
          icon={ShieldBan}
          title='隔离区'
          badge={<Badge variant='outline'>长期隔离</Badge>}
          summary='永久隔离账号并停用上游，不删除 grok2api 账号，也不会按停用时长自动恢复。'
          conditions={[
            thresholds.autoIsolationEnabled
              ? `当前已启用自动移入：探针判定达到「${autoIsolationMinStatusLabel(thresholds.autoIsolationMinStatus)}」及更严重时会停用上游并移入隔离区`
              : '当前未启用自动移入，只能从账号探针人工移入',
            '恢复上游需确认；也可只删除本系统评估、样本和告警记录',
          ]}
          tone='danger'
        />
        <RuleCard
          icon={Database}
          title='状态持久化'
          badge={<Badge variant='outline'>ORM</Badge>}
          summary='任务结束后计算一次并写入本地数据库，不依赖页面临时计算。'
          conditions={[
            '后台停止后已有结果仍保留',
            '样本过期不会按时钟自动降分，下一次任务结束时重新计算',
          ]}
          tone='info'
        />
      </div>
    </TitledCard>
  )
}

function RiskFormula({ thresholds }: { thresholds: Thresholds }) {
  const rows = [
    [
      '信号率',
      `降智信号数 ÷ 可测样本数 × ${formatNumber(thresholds.riskAnomalyRateWeight)}`,
      `满占比 ${formatNumber(thresholds.riskAnomalyRateWeight)}`,
    ],
    [
      '强信号',
      `强降智信号数 × ${formatNumber(thresholds.riskHardWeight)}`,
      `最高 ${formatNumber(thresholds.riskHardCap)}`,
    ],
    [
      '持续高速',
      `fast_risk 数 × ${formatNumber(thresholds.riskFastWeight)}`,
      `最高 ${formatNumber(thresholds.riskFastCap)}`,
    ],
    [
      '标记缺失',
      `marker_miss 数 × ${formatNumber(thresholds.riskMarkerMissWeight)}`,
      `最高 ${formatNumber(thresholds.riskMarkerMissCap)}`,
    ],
    [
      '连续信号',
      `最近连续降智信号数 × ${formatNumber(thresholds.riskStreakWeight)}`,
      `最高 ${formatNumber(thresholds.riskStreakCap)}`,
    ],
  ]

  return (
    <TitledCard
      icon={<Calculator />}
      iconTone='violet'
      title='风险分公式'
      description={`各项相加后封顶 ${formatNumber(thresholds.riskScoreCap)} 分；“观察”最低显示 ${formatNumber(thresholds.riskWatchFloor)} 分，“疑似降智”最低显示 ${formatNumber(thresholds.riskSuspectFloor)} 分，“高风险”最低显示 ${formatNumber(thresholds.riskHighFloor)} 分。公式因子来自系统设置，周期内固定出口和临时切换出口样本均参与计算。`}
    >
      <div className='grid gap-2 md:grid-cols-2 xl:grid-cols-3'>
        {rows.map(([label, formula, cap]) => (
          <div key={label} className='rounded-lg border px-3 py-2.5'>
            <div className='flex items-center justify-between gap-2'>
              <span className='text-sm font-medium'>{label}</span>
              <Badge variant='secondary'>{cap}</Badge>
            </div>
            <div className='mt-1.5 font-mono text-xs text-muted-foreground'>
              {formula}
            </div>
          </div>
        ))}
      </div>
    </TitledCard>
  )
}

function SampleMetricRules({ thresholds }: { thresholds: Thresholds }) {
  return (
    <div className='grid gap-4 lg:grid-cols-2'>
      <TitledCard
        icon={<Gauge />}
        iconTone='sky'
        title='TPS 计算'
        description='只计算首 Token 到达后的生成阶段。'
      >
        <div className='rounded-lg bg-muted/50 px-3 py-3 font-mono text-sm'>
          TPS = output_tokens × 1000 ÷ (duration_ms − first_token_ms)
        </div>
        <p className='mt-3 text-xs leading-5 text-muted-foreground'>
          output_tokens 包含上游 usage 返回的推理
          Token；因此长时间等待后集中返回大量 Token
          会产生很高的计算值，这正是缓冲型降智信号要捕获的特征。
        </p>
      </TitledCard>
      <TitledCard
        icon={<Timer />}
        iconTone='amber'
        title='缓冲特征'
        description='满足任意一项即认为存在集中吐出。'
        contentClassName='space-y-2'
      >
        <ConditionLine
          icon={Clock3}
          text={`首 Token 耗时 ÷ 总耗时 ≥ ${formatPercent(thresholds.bufferFirstTokenShare)}`}
        />
        <ConditionLine
          icon={Zap}
          text={`首 Token 后的生成阶段 < ${thresholds.minGenerationMs} ms`}
        />
      </TitledCard>
    </div>
  )
}

function SampleClassificationRules({ thresholds }: { thresholds: Thresholds }) {
  const rules: RuleCardProps[] = [
    {
      icon: XCircle,
      title: '请求错误',
      badge: <StatusBadge value='error' />,
      summary: 'HTTP 非 2xx，或请求、审计与集成步骤抛出错误。',
      conditions: ['不记录降智信号', '不会打断既有连续信号序列'],
      tone: 'danger',
    },
    {
      icon: CircleDashed,
      title: '无法测量',
      badge: <StatusBadge value='unmeasurable' />,
      summary: '缺少首 Token 时间，或总耗时没有大于首 Token 耗时。',
      conditions: ['TPS 记为 0', '不记录降智信号'],
    },
    {
      icon: ShieldAlert,
      title: '预期缺失',
      badge: <StatusBadge value='marker_miss' />,
      summary: '探针方案设置的自动校验标记没有出现在回复中。',
      conditions: [
        '匹配时忽略大小写、全角半角和空白',
        '优先于长度和 TPS 判断',
        '计为强降智信号',
      ],
      tone: 'danger',
    },
    {
      icon: Layers3,
      title: '样本不足',
      badge: <StatusBadge value='insufficient' />,
      summary: `输出 Token 少于 ${thresholds.minimumOutputTokens}，证据长度不足；短回复的生成窗口只有几毫秒，不评估 TPS 与思考输出，请求审计同样适用。`,
      conditions: [
        '任务中心标记为异常提示',
        '不计入账号降智信号',
        '不计入可测样本与异常占比分母',
        '不会打断既有连续信号序列',
        '注册联动不会恢复 grok2api 优先级',
      ],
      tone: 'warning',
    },
    {
      icon: Activity,
      title: '思考输出为 0',
      badge: <StatusBadge value='reasoning_zero' />,
      summary:
        '仅在模型能力策略适用、字段明确上报且输出达到策略下限时检查 reasoning Token。',
      conditions: thresholds.reasoningZeroRiskEnabled
        ? [
            '单次为 0 先记录观察信号',
            '同账号、稳定模型和请求类型连续达到策略次数后升级为强信号',
          ]
        : ['当前规则已关闭'],
      tone: thresholds.reasoningZeroRiskEnabled ? 'warning' : 'default',
    },
    {
      icon: CheckCircle2,
      title: '正常',
      badge: <StatusBadge value='normal' />,
      summary: `输出足够，且 TPS 低于 ${thresholds.degradationTps}。`,
      conditions: ['不记录降智信号', '会结束连续降智信号序列'],
      tone: 'success',
    },
    {
      icon: Gauge,
      title: '降智信号',
      badge: <StatusBadge value='elevated' />,
      summary: `TPS 位于 ${thresholds.degradationTps}～${thresholds.strongDegradationTps}，且没有缓冲特征。`,
      conditions: ['记录 1 次疑似降智', '不属于强降智信号'],
      tone: 'warning',
    },
    {
      icon: Timer,
      title: '缓冲降智信号',
      badge: <StatusBadge value='buffered_soft' />,
      summary: `TPS 位于 ${thresholds.degradationTps}～${thresholds.strongDegradationTps}，同时命中缓冲特征。`,
      conditions: ['记录 1 次疑似降智', '不属于强降智信号'],
      tone: 'warning',
    },
    {
      icon: AlertTriangle,
      title: '强缓冲降智信号',
      badge: <StatusBadge value='buffered_hard' />,
      summary: `TPS 达到 ${thresholds.strongDegradationTps}，并命中缓冲特征。`,
      conditions: ['记录 1 次疑似降智', '同时计为强降智信号'],
      tone: 'danger',
    },
    {
      icon: Zap,
      title: '强降智信号',
      badge: <StatusBadge value='fast_risk' />,
      summary: `TPS 达到 ${thresholds.strongDegradationTps}，但没有集中吐出的缓冲特征。`,
      conditions: ['记录 1 次疑似降智', '同时计为强降智信号'],
      tone: 'danger',
    },
  ]

  return (
    <TitledCard
      icon={<Gauge />}
      iconTone='primary'
      title='单次样本分类'
      description='规则按以下优先级依次判断，命中后不再继续向下分类。'
    >
      <div className='grid gap-3 lg:grid-cols-2 xl:grid-cols-3'>
        {rules.map((rule) => (
          <RuleCard key={rule.title} {...rule} />
        ))}
      </div>
    </TitledCard>
  )
}

function TaskStatusRules() {
  const rules: RuleCardProps[] = [
    {
      icon: Clock3,
      title: '排队中',
      badge: <StatusBadge value='queued' />,
      summary: '任务已持久化，等待工作线程领取。',
      conditions: [
        '同账号只允许一个任务执行',
        '队列优先级和创建时间决定领取顺序',
      ],
      tone: 'warning',
    },
    {
      icon: LoaderCircle,
      title: '执行中',
      badge: <StatusBadge value='running' />,
      summary: '工作线程已领取任务，正在逐轮、逐出口执行。',
      conditions: ['持续写入当前轮次、目标和心跳'],
      tone: 'info',
    },
    {
      icon: Ban,
      title: '取消中',
      badge: <StatusBadge value='cancel_requested' />,
      summary: '运行中任务收到取消请求，等待当前调用退出并执行清理恢复。',
      conditions: ['账号设置恢复完成后才进入最终状态'],
      tone: 'warning',
    },
    {
      icon: RefreshCw,
      title: '恢复中',
      badge: <StatusBadge value='recovering' />,
      summary: '服务启动时发现上次中断的活动任务，优先恢复账号原设置。',
      conditions: [
        '恢复成功后重新排队或结束',
        '恢复失败则任务失败并等待人工同步',
      ],
      tone: 'info',
    },
    {
      icon: CheckCircle2,
      title: '已完成',
      badge: <StatusBadge value='completed' />,
      summary: '所有步骤完成，样本与账号设置恢复均没有错误。',
      tone: 'success',
    },
    {
      icon: AlertTriangle,
      title: '部分异常',
      badge: <StatusBadge value='completed_with_errors' />,
      summary: '任务流程走完，但存在样本错误或资源清理警告。',
      conditions: ['成功样本仍然保留并参与分析'],
      tone: 'warning',
    },
    {
      icon: XCircle,
      title: '失败',
      badge: <StatusBadge value='failed' />,
      summary: '准备、路由、执行或启动恢复阶段发生致命错误。',
      tone: 'danger',
    },
    {
      icon: Ban,
      title: '已取消',
      badge: <StatusBadge value='cancelled' />,
      summary: '排队任务被直接取消，或运行任务完成中止与清理。',
    },
  ]

  return (
    <TitledCard
      icon={<Activity />}
      iconTone='indigo'
      title='任务状态'
      description='任务状态描述队列和执行生命周期，不等于样本质量分类。'
    >
      <div className='grid gap-3 lg:grid-cols-2 xl:grid-cols-4'>
        {rules.map((rule) => (
          <RuleCard key={rule.title} {...rule} />
        ))}
      </div>
    </TitledCard>
  )
}

function RestoreStatusRules() {
  const rules: RuleCardProps[] = [
    {
      icon: CircleDashed,
      title: '未记录',
      badge: <Badge variant='secondary'>not_recorded</Badge>,
      summary: '正常定检等未修改账号设置的任务不需要进入恢复流程。',
    },
    {
      icon: Clock3,
      title: '等待恢复',
      badge: <Badge variant='warning'>pending</Badge>,
      summary: '已保存启停、优先级、并发和出口快照，任务结束时自动恢复。',
      tone: 'warning',
    },
    {
      icon: LoaderCircle,
      title: '正在恢复',
      badge: <Badge variant='info'>restoring</Badge>,
      summary: '正在向 grok2api 回写任务开始前的账号设置。',
      tone: 'info',
    },
    {
      icon: ShieldCheck,
      title: '自动恢复完成',
      badge: <Badge variant='success'>automatic</Badge>,
      summary: '任务收尾阶段自动恢复成功。',
      tone: 'success',
    },
    {
      icon: RefreshCw,
      title: '启动恢复完成',
      badge: <Badge variant='info'>startup</Badge>,
      summary: '服务重启后识别到中断任务，并按快照恢复成功。',
      tone: 'info',
    },
    {
      icon: Undo2,
      title: '人工同步完成',
      badge: <Badge variant='secondary'>manual</Badge>,
      summary: '操作员从任务详情主动执行原设置同步。',
    },
    {
      icon: ShieldAlert,
      title: '需要人工同步',
      badge: <Badge variant='destructive'>restore_failed</Badge>,
      summary: '自动回写失败；同账号后续执行、重试和删除会被阻止。',
      conditions: ['在任务详情点击同步原设置后解除阻塞'],
      tone: 'danger',
    },
  ]

  return (
    <TitledCard
      icon={<Undo2 />}
      iconTone='cyan'
      title='账号设置恢复状态'
      description='这是任务对 grok2api 临时修改的补偿状态，与账号风险判定相互独立。'
    >
      <div className='grid gap-3 lg:grid-cols-2 xl:grid-cols-3'>
        {rules.map((rule) => (
          <RuleCard key={rule.title} {...rule} />
        ))}
      </div>
    </TitledCard>
  )
}

function UpstreamStatusRules() {
  return (
    <TitledCard
      icon={<KeyRound />}
      iconTone='blue'
      title='grok2api 上游字段'
      description='这些字段来自实时账号列表，各自表达不同维度，不应合并理解。'
    >
      <div className='grid gap-3 lg:grid-cols-2 xl:grid-cols-3'>
        <RuleCard
          icon={Activity}
          title='enabled'
          badge={
            <span className='flex items-center gap-1'>
              <EnabledBadge enabled />
              <EnabledBadge enabled={false} />
            </span>
          }
          summary='表示账号是否参与 grok2api 正常调度，不表示凭据有效或模型质量正常。'
          conditions={[
            '正常定检只巡检已启用账号，不会修改启停状态',
            '人工诊断可短时激活停用账号，结束后恢复原值',
          ]}
          tone='info'
        />
        <RuleCard
          icon={KeyRound}
          title='authStatus = active'
          badge={<Badge variant='success'>鉴权有效</Badge>}
          summary='grok2api 当前认为账号凭据仍可使用。'
          conditions={['不代表 enabled=true', '不代表没有降智']}
          tone='success'
        />
        <RuleCard
          icon={ShieldAlert}
          title='reauthRequired'
          badge={<Badge variant='destructive'>需要重新授权</Badge>}
          summary='凭据失效或需要重新授权，账号不会进入探针执行条件。'
          tone='danger'
        />
        <RuleCard
          icon={ShieldCheck}
          title='账号当前出口'
          badge={<Badge variant='success'>正常定检</Badge>}
          summary='每次执行实时读取账号当前绑定节点，不切换出口、不解绑，也不回写账号设置。'
          conditions={[
            '仅巡检已启用且已绑定固定出口的账号',
            '请求后核验 grok2api 审计中的实际账号与节点',
            '审计不一致时样本记为错误，不参与风险分母',
          ]}
          tone='success'
        />
        <RuleCard
          icon={Route}
          title='上游调度'
          badge={<Badge variant='warning'>诊断操作</Badge>}
          summary='临时解除账号固定绑定，由 grok2api 的节点池、回退或本地出口策略决定。'
          conditions={['只应在异常诊断中使用', '任务结束后恢复原绑定']}
          tone='warning'
        />
        <RuleCard
          icon={Network}
          title='指定固定出口'
          badge={<Badge variant='warning'>诊断操作</Badge>}
          summary='探针临时把账号绑定到指定 grok_build 出口节点。'
          conditions={[
            '审计实际节点与目标节点不同时样本记为错误',
            '任务结束后恢复原绑定',
          ]}
          tone='warning'
        />
        <RuleCard
          icon={Zap}
          title='快速出口质量探针'
          badge={<Badge variant='info'>quality-test</Badge>}
          summary='诊断指定节点的 quality-test 接口，因此必须选择已配置代理的出口节点。'
          conditions={[
            '提示词只要求输出几个字，用于出口连通性与标记校验',
            '输出通常达不到最低 Token，不检测思考缺失，也不评估 TPS',
            '没有诊断出口时改用完整对话的账号当前出口定检',
          ]}
          tone='info'
        />
        <RuleCard
          icon={Clock3}
          title='账号限定范围暂时不可用'
          badge={<Badge variant='outline'>HTTP 503</Badge>}
          summary='Client Key 与临时模型路由限定的账号范围内，此刻没有账号能被调度；这与“剩余额度为 0”不是同一个条件。'
          conditions={[
            '常见于前一个网络失败触发账号短冷却、账号被停用、鉴权状态变化或路由范围不匹配',
            '错误码 client_key_account_scope_unavailable',
            '无上游等待提示时按设置退避重试，不记录降智信号',
          ]}
          tone='warning'
        />
        <RuleCard
          icon={Timer}
          title='账号冷却'
          badge={<Badge variant='outline'>HTTP 429</Badge>}
          summary='上游账号仍有额度，但 grok2api 因最近失败暂时不再选择它。'
          conditions={[
            '错误码 upstream_cooling 或 upstream_model_cooling',
            '有效 Retry-After 或账号冷却时间优先，不受本地退避上限截断',
            '最终仍在冷却时，下一轮会等待冷却结束再开始',
            '冷却错误不参与降智判定',
          ]}
          tone='warning'
        />
        <RuleCard
          icon={XCircle}
          title='上游网络错误'
          badge={<Badge variant='outline'>HTTP 502</Badge>}
          summary='请求已命中账号或出口，但到上游的传输失败；失败出口可能进一步触发账号短冷却。'
          conditions={[
            '错误码 upstream_network_error',
            '优先检查任务审计中的实际出口与出口健康状态',
            '属于可用性证据，不属于 TPS 降智证据',
          ]}
          tone='danger'
        />
      </div>
    </TitledCard>
  )
}

function FlowStep({
  icon: Icon,
  title,
  description,
  tone = 'primary',
}: {
  icon: ElementType
  title: string
  description: string
  tone?: IconBadgeTone
}) {
  return (
    <div className='flex items-center gap-3 rounded-lg border px-3 py-2.5'>
      <IconBadge tone={tone} size='md'>
        <Icon />
      </IconBadge>
      <div className='min-w-0'>
        <div className='text-sm font-medium'>{title}</div>
        <div className='truncate text-xs text-muted-foreground'>
          {description}
        </div>
      </div>
    </div>
  )
}

function FlowArrow() {
  return (
    <ArrowRight className='mx-auto hidden size-4 text-muted-foreground md:block' />
  )
}

function RuleCard({
  icon: Icon,
  title,
  badge,
  summary,
  conditions = [],
  tone = 'default',
}: RuleCardProps) {
  return (
    <Card className='gap-3 py-3 shadow-none'>
      <CardHeader className='px-3.5 py-0'>
        <div className='flex items-start justify-between gap-3'>
          <div className='flex min-w-0 items-center gap-2.5'>
            <IconBadge tone={ruleIconTone[tone]} size='sm'>
              <Icon />
            </IconBadge>
            <CardTitle className='truncate text-sm'>{title}</CardTitle>
          </div>
          {badge}
        </div>
      </CardHeader>
      <CardContent className='px-3.5'>
        <p className='text-sm leading-6 text-muted-foreground'>{summary}</p>
        {conditions.length > 0 && (
          <ul className='mt-2.5 space-y-1.5'>
            {conditions.map((condition) => (
              <li key={condition} className='flex gap-2 text-xs leading-5'>
                <span className='mt-2 size-1 shrink-0 rounded-full bg-muted-foreground/40' />
                <span>{condition}</span>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}

function ConditionLine({
  icon: Icon,
  text,
}: {
  icon: ElementType
  text: string
}) {
  return (
    <div className='flex items-center gap-3 rounded-lg border px-3 py-2.5 text-sm'>
      <IconBadge tone='primary' size='sm'>
        <Icon />
      </IconBadge>
      <span>{text}</span>
    </div>
  )
}

function formatPercent(value: number) {
  return `${formatNumber(value * 100)}%`
}

function formatNumber(value: number) {
  return new Intl.NumberFormat('zh-CN', {
    maximumFractionDigits: 2,
  }).format(value)
}

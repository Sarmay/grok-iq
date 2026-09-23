import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Code2,
  Edit3,
  Eye,
  FileText,
  FlaskConical,
  Image,
  ListChecks,
  Plus,
  RefreshCw,
  ShieldCheck,
  Trash2,
  UserRound,
} from 'lucide-react'
import { toast } from 'sonner'
import { api, type ProbeProfile } from '@/lib/api'
import { cn, getErrorMessage } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { IconBadge } from '@/components/ui/icon-badge'
import { Input } from '@/components/ui/input'
import {
  Sheet,
  SheetClose,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { Switch } from '@/components/ui/switch'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import { ActionToolbar, ToolbarAction } from '@/components/action-toolbar'
import { ConfirmDialog } from '@/components/confirm-dialog'
import { EnabledBadge } from '@/components/enabled-badge'
import {
  FormattedContentPreviewButton,
  FormattedContentRenderer,
} from '@/components/formatted-content'
import { EmptyState, LoadingState, Page, PageHeader } from '@/components/page'
import { SelectionToolbar } from '@/components/selection-toolbar'

type ProfileView = 'built-in' | 'custom'

export function ProbeProfilesPage() {
  const queryClient = useQueryClient()
  const profiles = useQuery({ queryKey: ['profiles'], queryFn: api.profiles })
  const [profileView, setProfileView] = useState<ProfileView>('built-in')
  const [editingProfile, setEditingProfile] = useState<
    ProbeProfile | 'new' | null
  >(null)
  const [deletingProfile, setDeletingProfile] = useState<ProbeProfile | null>(
    null
  )
  const [selectedProfileIds, setSelectedProfileIds] = useState<string[]>([])
  const [bulkDeleteOpen, setBulkDeleteOpen] = useState(false)
  const profileItems = useMemo(() => profiles.data ?? [], [profiles.data])
  const builtInProfiles = useMemo(
    () => profileItems.filter((profile) => profile.built_in),
    [profileItems]
  )
  const customProfiles = useMemo(
    () => profileItems.filter((profile) => !profile.built_in),
    [profileItems]
  )
  const visibleProfiles =
    profileView === 'built-in' ? builtInProfiles : customProfiles
  const profileIdSet = useMemo(
    () => new Set(profileItems.map((profile) => profile.id)),
    [profileItems]
  )
  const selectedProfiles = visibleProfiles.filter((profile) =>
    selectedProfileIds.includes(profile.id)
  )
  const allVisibleProfilesSelected =
    visibleProfiles.length > 0 &&
    selectedProfiles.length === visibleProfiles.length

  useEffect(() => {
    // Keep selection aligned with the latest server-side profile list.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setSelectedProfileIds((current) => {
      const next = current.filter((id) => profileIdSet.has(id))
      return next.length === current.length ? current : next
    })
  }, [profileIdSet])

  const deleteMutation = useMutation({
    mutationFn: (profile: ProbeProfile) => api.deleteProfile(profile.id),
    onSuccess: () => {
      toast.success('方案已删除')
      setDeletingProfile(null)
      void queryClient.invalidateQueries({ queryKey: ['profiles'] })
    },
    onError: (error) => toast.error(getErrorMessage(error)),
  })
  const bulkDeleteMutation = useMutation({
    mutationFn: api.deleteProfiles,
    onSuccess: (result) => {
      setBulkDeleteOpen(false)
      setSelectedProfileIds(result.protectedIds ?? [])
      if (result.protected) {
        toast.warning(
          `已删除 ${result.deleted} 个方案；${result.protected} 个方案已有计划或历史任务，已跳过并保留选择`
        )
      } else {
        toast.success(`已删除 ${result.deleted} 个方案`)
      }
      void queryClient.invalidateQueries({ queryKey: ['profiles'] })
    },
    onError: (error) => toast.error(getErrorMessage(error)),
  })

  const toggleProfileSelection = (id: string, checked: boolean) => {
    setSelectedProfileIds((current) =>
      checked
        ? current.includes(id)
          ? current
          : [...current, id]
        : current.filter((value) => value !== id)
    )
  }

  const changeProfileView = (value: string) => {
    if (value !== 'built-in' && value !== 'custom') return
    setProfileView(value)
    setSelectedProfileIds([])
    setBulkDeleteOpen(false)
  }

  if (profiles.isLoading) {
    return (
      <Page>
        <LoadingState />
      </Page>
    )
  }

  return (
    <Page>
      <PageHeader
        title='探针方案'
        description='分别管理系统内置基线与用户自定义方案；Cron 计划只负责调度。'
        descriptionAsHint
        actions={
          <>
            <ActionToolbar label='探针方案操作'>
              <ToolbarAction
                label='刷新探针方案'
                pending={profiles.isFetching}
                onClick={() => void profiles.refetch()}
              >
                <RefreshCw />
              </ToolbarAction>
              <ToolbarAction
                label={
                  allVisibleProfilesSelected
                    ? '取消全选当前方案'
                    : '全选当前方案'
                }
                active={allVisibleProfilesSelected}
                disabled={
                  visibleProfiles.length === 0 || bulkDeleteMutation.isPending
                }
                onClick={() =>
                  setSelectedProfileIds(
                    allVisibleProfilesSelected
                      ? []
                      : visibleProfiles.map((profile) => profile.id)
                  )
                }
              >
                <ListChecks />
              </ToolbarAction>
              <ToolbarAction
                label='新建探针方案'
                disabled={bulkDeleteMutation.isPending}
                onClick={() => {
                  setProfileView('custom')
                  setSelectedProfileIds([])
                  setEditingProfile('new')
                }}
              >
                <Plus />
              </ToolbarAction>
            </ActionToolbar>
            <SelectionToolbar
              selectedCount={selectedProfileIds.length}
              entityLabel='方案'
              disabled={bulkDeleteMutation.isPending}
              onClear={() => setSelectedProfileIds([])}
            >
              <ToolbarAction
                label={`删除 ${selectedProfileIds.length} 个方案`}
                destructive
                disabled={bulkDeleteMutation.isPending}
                pending={bulkDeleteMutation.isPending}
                onClick={() => setBulkDeleteOpen(true)}
              >
                <Trash2 />
              </ToolbarAction>
            </SelectionToolbar>
          </>
        }
      />

      <Tabs
        value={profileView}
        onValueChange={changeProfileView}
        className='gap-4'
      >
        <TabsList className='w-full justify-start overflow-x-auto sm:w-fit'>
          <TabsTrigger value='built-in'>
            <ShieldCheck />
            系统内置
            <Badge
              variant='secondary'
              className='h-5 min-w-5 border-0 px-1.5 text-[11px] tabular-nums'
            >
              {builtInProfiles.length}
            </Badge>
          </TabsTrigger>
          <TabsTrigger value='custom'>
            <UserRound />
            用户自定义
            <Badge
              variant='secondary'
              className='h-5 min-w-5 border-0 px-1.5 text-[11px] tabular-nums'
            >
              {customProfiles.length}
            </Badge>
          </TabsTrigger>
        </TabsList>
        <TabsContent value='built-in' className='mt-0'>
          <ProfileGrid
            profiles={builtInProfiles}
            selectedProfileIds={selectedProfileIds}
            pending={deleteMutation.isPending || bulkDeleteMutation.isPending}
            emptyTitle='暂无系统内置方案'
            emptyDescription='后端当前没有可用的系统种子方案。'
            onSelectedChange={toggleProfileSelection}
            onEdit={setEditingProfile}
            onDelete={setDeletingProfile}
          />
        </TabsContent>
        <TabsContent value='custom' className='mt-0'>
          <ProfileGrid
            profiles={customProfiles}
            selectedProfileIds={selectedProfileIds}
            pending={deleteMutation.isPending || bulkDeleteMutation.isPending}
            emptyTitle='暂无用户自定义方案'
            emptyDescription='当前没有用户创建的探针方案。'
            onSelectedChange={toggleProfileSelection}
            onEdit={setEditingProfile}
            onDelete={setDeletingProfile}
          />
        </TabsContent>
      </Tabs>

      {editingProfile && (
        <ProfileDialog
          key={editingProfile === 'new' ? 'new' : editingProfile.id}
          open
          value={editingProfile}
          onOpenChange={(open) => !open && setEditingProfile(null)}
          onSaved={() => {
            setEditingProfile(null)
            void queryClient.invalidateQueries({ queryKey: ['profiles'] })
          }}
        />
      )}

      <ConfirmDialog
        open={deletingProfile != null}
        onOpenChange={(open) => !open && setDeletingProfile(null)}
        title='删除探针方案？'
        desc={
          <>
            将删除「{deletingProfile?.name}」。已经被 Cron 计划或历史任务引用的
            方案不能删除，请改为停用。
          </>
        }
        confirmText='删除方案'
        cancelBtnText='取消'
        destructive
        isLoading={deleteMutation.isPending}
        handleConfirm={() => {
          if (deletingProfile) deleteMutation.mutate(deletingProfile)
        }}
      />
      <ConfirmDialog
        open={bulkDeleteOpen}
        onOpenChange={(open) =>
          !bulkDeleteMutation.isPending && setBulkDeleteOpen(open)
        }
        title={`删除 ${selectedProfileIds.length} 个探针方案？`}
        desc='仅删除未被 Cron 计划和历史任务引用的方案；仍在使用的方案会自动跳过并保留选择。'
        confirmText={
          <>
            <Trash2 />
            删除方案
          </>
        }
        cancelBtnText='取消'
        destructive
        isLoading={bulkDeleteMutation.isPending}
        disabled={selectedProfileIds.length === 0}
        handleConfirm={() => bulkDeleteMutation.mutate(selectedProfileIds)}
      />
    </Page>
  )
}

function ProfileGrid({
  profiles,
  selectedProfileIds,
  pending,
  emptyTitle,
  emptyDescription,
  onSelectedChange,
  onEdit,
  onDelete,
}: {
  profiles: ProbeProfile[]
  selectedProfileIds: string[]
  pending: boolean
  emptyTitle: string
  emptyDescription: string
  onSelectedChange: (id: string, checked: boolean) => void
  onEdit: (profile: ProbeProfile) => void
  onDelete: (profile: ProbeProfile) => void
}) {
  if (!profiles.length) {
    return <EmptyState title={emptyTitle} description={emptyDescription} />
  }

  return (
    <div className='grid gap-3 lg:grid-cols-2'>
      {profiles.map((profile) => (
        <ProfileCard
          key={profile.id}
          profile={profile}
          selected={selectedProfileIds.includes(profile.id)}
          pending={pending}
          onSelectedChange={(checked) => onSelectedChange(profile.id, checked)}
          onEdit={() => onEdit(profile)}
          onDelete={() => onDelete(profile)}
        />
      ))}
    </div>
  )
}

function ProfileCard({
  profile,
  selected,
  pending,
  onSelectedChange,
  onEdit,
  onDelete,
}: {
  profile: ProbeProfile
  selected: boolean
  pending: boolean
  onSelectedChange: (checked: boolean) => void
  onEdit: () => void
  onDelete: () => void
}) {
  return (
    <Card
      className={cn(
        'gap-3 py-3 transition-colors',
        selected && 'border-primary/40 bg-primary/[0.025]'
      )}
    >
      <CardHeader className='px-4'>
        <div className='flex items-start gap-3'>
          <Checkbox
            checked={selected}
            disabled={pending}
            onCheckedChange={(value) => onSelectedChange(value === true)}
            aria-label={`选择方案 ${profile.name}`}
            className='mt-2'
          />
          <IconBadge tone={profile.built_in ? 'sky' : 'violet'}>
            {profile.built_in ? <ShieldCheck /> : <FlaskConical />}
          </IconBadge>
          <div className='min-w-0 flex-1'>
            <div className='flex flex-wrap items-center gap-1.5'>
              <CardTitle className='truncate text-sm'>{profile.name}</CardTitle>
              <Badge
                variant={profile.built_in ? 'info' : 'outline'}
                className='h-5 px-1.5 text-[11px]'
              >
                {profile.built_in ? '系统内置' : '用户自定义'}
              </Badge>
              <EnabledBadge enabled={profile.enabled} />
            </div>
            <CardDescription className='mt-1 line-clamp-1 text-xs'>
              {profile.description || '未填写说明'}
            </CardDescription>
          </div>
          <ActionToolbar label={`${profile.name} 操作`}>
            {profile.expected_output ? (
              <FormattedContentPreviewButton
                content={profile.expected_output}
                expectedImageUrl={profile.expected_image_url}
                label='预览预期结果'
                title={`${profile.name} · 预期结果`}
                iconOnly
                variant='ghost'
                className='size-7'
              />
            ) : null}
            <ToolbarAction label='编辑方案' disabled={pending} onClick={onEdit}>
              <Edit3 />
            </ToolbarAction>
            <ToolbarAction
              label='删除方案'
              destructive
              disabled={pending}
              onClick={onDelete}
            >
              <Trash2 />
            </ToolbarAction>
          </ActionToolbar>
        </div>
      </CardHeader>
      <CardContent className='space-y-3 px-4'>
        <div className='rounded-lg border bg-muted/20 px-3 py-2 text-sm'>
          <div className='font-mono text-[11px] text-primary'>
            {profile.model}
          </div>
          <div className='mt-1.5 line-clamp-3 text-xs leading-5 whitespace-pre-wrap'>
            {profile.prompt}
          </div>
        </div>
        <div className='flex flex-wrap gap-1.5'>
          <Badge variant='outline' className='h-5 px-1.5 text-[11px]'>
            {profile.max_output_tokens > 0
              ? `max ${profile.max_output_tokens} tokens`
              : '跟随上游'}
          </Badge>
          {profile.expected_text && (
            <Badge variant='outline' className='h-5 px-1.5 text-[11px]'>
              校验 {profile.expected_text}
            </Badge>
          )}
          {profile.expected_output && (
            <Badge variant='info' className='h-5 px-1.5 text-[11px]'>
              <FileText className='size-3' />
              预期结果
            </Badge>
          )}
          {profile.expected_image_url && (
            <Badge variant='info' className='h-5 px-1.5 text-[11px]'>
              <Image className='size-3' />
              预期图片
            </Badge>
          )}
        </div>
      </CardContent>
    </Card>
  )
}

function ProfileDialog({
  open,
  value,
  onOpenChange,
  onSaved,
}: {
  open: boolean
  value: ProbeProfile | 'new'
  onOpenChange: (open: boolean) => void
  onSaved: () => void
}) {
  const initial = value === 'new' ? null : value
  const [form, setForm] = useState(() => profileForm(initial))
  const [followUpstreamLimit, setFollowUpstreamLimit] = useState(
    !initial || initial.max_output_tokens === 0
  )
  const [customTokenLimit, setCustomTokenLimit] = useState(() =>
    String(
      initial && initial.max_output_tokens > 0
        ? initial.max_output_tokens
        : 4096
    )
  )
  const [expectedOutputEditorOpen, setExpectedOutputEditorOpen] =
    useState(false)
  const [errors, setErrors] = useState<ProfileFormErrors>({})
  const fieldRefs = useRef<
    Partial<Record<ProfileFormErrorKey, HTMLInputElement | HTMLTextAreaElement>>
  >({})

  const mutation = useMutation({
    mutationFn: (body: Record<string, unknown>) => {
      return initial
        ? api.updateProfile(initial.id, body)
        : api.createProfile(body)
    },
    onSuccess: () => {
      toast.success(initial ? '方案已更新' : '方案已创建')
      onSaved()
    },
    onError: (error) => toast.error(getErrorMessage(error)),
  })

  const set = (key: keyof typeof form, value: string | number | boolean) => {
    setForm((current) => ({ ...current, [key]: value }))
    setErrors((current) => ({
      ...current,
      [key]: undefined,
    }))
  }

  const handleSave = () => {
    const result = validateProfileForm({
      form,
      followUpstreamLimit,
      customTokenLimit,
    })
    setErrors(result.errors)
    const firstError = PROFILE_ERROR_ORDER.find((key) => result.errors[key])
    if (firstError) {
      requestAnimationFrame(() => fieldRefs.current[firstError]?.focus())
      return
    }
    mutation.mutate(result.body!)
  }

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent size='wide'>
          <DialogHeader>
            <DialogTitle>
              {initial ? '编辑探针方案' : '新建探针方案'}
            </DialogTitle>
            <DialogDescription>
              自动校验标记用于程序判定；预期结果支持 HTML、Markdown
              和普通文本渲染。
              <span className='mt-1 block'>
                带 <span className='font-medium text-destructive'>*</span>{' '}
                的字段为必填项。
              </span>
            </DialogDescription>
          </DialogHeader>
          <div className='grid max-h-[68dvh] min-h-0 gap-4 overflow-auto py-2 sm:grid-cols-2'>
            <Field
              label='名称'
              htmlFor='profile-name'
              required
              error={errors.name}
              errorId='profile-name-error'
            >
              <Input
                id='profile-name'
                ref={(node) => {
                  if (node) fieldRefs.current.name = node
                }}
                value={form.name}
                onChange={(event) => set('name', event.target.value)}
                placeholder='例如：HTML 生成质量探针'
                aria-invalid={Boolean(errors.name)}
                aria-describedby={
                  errors.name ? 'profile-name-error' : undefined
                }
              />
            </Field>
            <Field
              label='模型'
              htmlFor='profile-model'
              required
              error={errors.model}
              errorId='profile-model-error'
            >
              <Input
                id='profile-model'
                ref={(node) => {
                  if (node) fieldRefs.current.model = node
                }}
                value={form.model}
                onChange={(event) => set('model', event.target.value)}
                placeholder='例如：grok-4.5'
                aria-invalid={Boolean(errors.model)}
                aria-describedby={
                  errors.model ? 'profile-model-error' : undefined
                }
              />
            </Field>
            <Field
              label='说明'
              htmlFor='profile-description'
              className='sm:col-span-2'
            >
              <Input
                id='profile-description'
                value={form.description}
                onChange={(event) => set('description', event.target.value)}
                placeholder='说明该方案用于观察哪些输出能力或异常信号'
              />
            </Field>
            <Field
              label='系统提示'
              htmlFor='profile-system-prompt'
              className='sm:col-span-2'
            >
              <Textarea
                id='profile-system-prompt'
                value={form.system_prompt}
                onChange={(event) => set('system_prompt', event.target.value)}
                placeholder='可选，例如：请严格遵循用户指定的格式要求'
              />
            </Field>
            <Field
              label='测试提示词'
              htmlFor='profile-prompt'
              required
              error={errors.prompt}
              errorId='profile-prompt-error'
              className='sm:col-span-2'
            >
              <Textarea
                id='profile-prompt'
                ref={(node) => {
                  if (node) fieldRefs.current.prompt = node
                }}
                value={form.prompt}
                onChange={(event) => set('prompt', event.target.value)}
                placeholder='输入每轮探测时发送给模型的提示词'
                className='min-h-28'
                aria-invalid={Boolean(errors.prompt)}
                aria-describedby={
                  errors.prompt ? 'profile-prompt-error' : undefined
                }
              />
            </Field>
            <Field
              label='自动校验标记'
              htmlFor='profile-expected-text'
              description='回复包含该字符串时记为匹配，忽略大小写、全角半角和空白；留空则跳过此项自动校验。'
            >
              <Input
                id='profile-expected-text'
                value={form.expected_text}
                onChange={(event) => set('expected_text', event.target.value)}
                placeholder='例如：探针校验通过'
              />
            </Field>
            <Field
              label='参考图片 URL（可选）'
              htmlFor='profile-expected-image'
              description='作为补充视觉参考，可在预期结果预览中并排查看。'
            >
              <Input
                id='profile-expected-image'
                value={form.expected_image_url}
                onChange={(event) =>
                  set('expected_image_url', event.target.value)
                }
                placeholder='https://example.com/reference.png'
              />
            </Field>
            <Field
              label='预期结果内容'
              description='可输入完整 HTML、HTML 片段、Markdown 或普通文本；长内容在抽屉中编辑。'
              className='sm:col-span-2'
            >
              <div className='flex flex-col gap-3 rounded-lg border bg-muted/20 p-3 sm:flex-row sm:items-center sm:justify-between'>
                <div className='min-w-0'>
                  <div className='text-sm font-medium'>
                    {form.expected_output.trim()
                      ? `已填写 ${form.expected_output.length.toLocaleString()} 个字符`
                      : '尚未填写预期结果'}
                  </div>
                  <div className='mt-1 truncate text-xs text-muted-foreground'>
                    {form.expected_output.trim().split(/\r?\n/, 1)[0] ||
                      '编辑后可实时切换到渲染预览。'}
                  </div>
                </div>
                <div className='flex shrink-0 flex-wrap gap-2'>
                  <Button
                    type='button'
                    size='sm'
                    variant='outline'
                    onClick={() => setExpectedOutputEditorOpen(true)}
                  >
                    <Edit3 />
                    编辑内容
                  </Button>
                  <FormattedContentPreviewButton
                    content={form.expected_output}
                    expectedImageUrl={form.expected_image_url}
                    label='预览结果'
                    title='预期结果预览'
                    showWhenEmpty
                  />
                </div>
              </div>
            </Field>
            <Field
              label='输出 Token'
              htmlFor='profile-output-tokens'
              error={errors.max_output_tokens}
              errorId='profile-output-tokens-error'
            >
              <div className='flex min-w-0 items-center gap-3'>
                <Input
                  id='profile-output-tokens'
                  ref={(node) => {
                    if (node) fieldRefs.current.max_output_tokens = node
                  }}
                  type='number'
                  min={1}
                  value={followUpstreamLimit ? '' : customTokenLimit}
                  onChange={(event) => {
                    setCustomTokenLimit(event.target.value)
                    setErrors((current) => ({
                      ...current,
                      max_output_tokens: undefined,
                    }))
                  }}
                  placeholder='由上游决定'
                  disabled={followUpstreamLimit}
                  className='min-w-0 flex-1'
                  aria-invalid={Boolean(errors.max_output_tokens)}
                  aria-describedby={
                    errors.max_output_tokens
                      ? 'profile-output-tokens-error'
                      : undefined
                  }
                />
                <label className='flex shrink-0 cursor-pointer items-center gap-2 text-sm'>
                  <Switch
                    checked={followUpstreamLimit}
                    onCheckedChange={(checked) => {
                      setFollowUpstreamLimit(checked)
                      if (checked) {
                        setErrors((current) => ({
                          ...current,
                          max_output_tokens: undefined,
                        }))
                      }
                    }}
                  />
                  跟随上游
                </label>
              </div>
            </Field>
            <Field
              label='Temperature'
              htmlFor='profile-temperature'
              error={errors.temperature}
              errorId='profile-temperature-error'
            >
              <Input
                id='profile-temperature'
                ref={(node) => {
                  if (node) fieldRefs.current.temperature = node
                }}
                value={form.temperature}
                onChange={(event) => set('temperature', event.target.value)}
                placeholder='留空使用默认值'
                aria-invalid={Boolean(errors.temperature)}
                aria-describedby={
                  errors.temperature ? 'profile-temperature-error' : undefined
                }
              />
            </Field>
            <Field
              label='附加请求体 JSON'
              htmlFor='profile-extra-body'
              error={errors.extra_body}
              errorId='profile-extra-body-error'
              className='sm:col-span-2'
            >
              <Textarea
                id='profile-extra-body'
                ref={(node) => {
                  if (node) fieldRefs.current.extra_body = node
                }}
                value={form.extra_body}
                onChange={(event) => set('extra_body', event.target.value)}
                placeholder='{}'
                className='min-h-28 font-mono text-xs'
                aria-invalid={Boolean(errors.extra_body)}
                aria-describedby={
                  errors.extra_body ? 'profile-extra-body-error' : undefined
                }
              />
            </Field>
            <label className='flex items-center gap-3 rounded-lg border px-3 py-2.5 text-sm sm:col-span-2'>
              <Switch
                checked={form.enabled}
                onCheckedChange={(value) => set('enabled', value)}
              />
              <EnabledBadge enabled={form.enabled} />
            </label>
          </div>
          <DialogFooter>
            <Button variant='outline' onClick={() => onOpenChange(false)}>
              取消
            </Button>
            <Button disabled={mutation.isPending} onClick={handleSave}>
              {mutation.isPending ? '保存中…' : '保存方案'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <ExpectedOutputEditorSheet
        open={expectedOutputEditorOpen}
        onOpenChange={setExpectedOutputEditorOpen}
        value={form.expected_output}
        onChange={(value) => set('expected_output', value)}
      />
    </>
  )
}

function ExpectedOutputEditorSheet({
  open,
  onOpenChange,
  value,
  onChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  value: string
  onChange: (value: string) => void
}) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side='right'
        className='w-[min(64rem,calc(100vw-1rem))] gap-0 p-0 sm:max-w-[64rem]'
      >
        <SheetHeader className='shrink-0 border-b pe-12'>
          <SheetTitle>编辑预期结果</SheetTitle>
          <SheetDescription>
            内容保存在当前探针方案中，任务中心可用同一预览器查看。
          </SheetDescription>
        </SheetHeader>
        <Tabs
          defaultValue='edit'
          className='flex min-h-0 flex-1 flex-col gap-0'
        >
          <div className='shrink-0 border-b px-4 py-3'>
            <TabsList>
              <TabsTrigger value='edit'>
                <Code2 />
                编辑
              </TabsTrigger>
              <TabsTrigger value='preview'>
                <Eye />
                渲染预览
              </TabsTrigger>
            </TabsList>
          </div>
          <TabsContent
            value='edit'
            className='m-0 min-h-0 flex-1 overflow-hidden p-4'
          >
            <Textarea
              value={value}
              onChange={(event) => onChange(event.target.value)}
              placeholder='输入 HTML、Markdown 或普通文本…'
              spellCheck={false}
              className='h-full min-h-0 resize-none font-mono text-xs leading-5'
            />
          </TabsContent>
          <TabsContent
            value='preview'
            className='m-0 min-h-0 flex-1 overflow-hidden p-4'
          >
            <FormattedContentRenderer
              content={value}
              emptyText='填写内容后即可在这里渲染预览'
              className='h-full min-h-0'
            />
          </TabsContent>
        </Tabs>
        <SheetFooter className='shrink-0 flex-row items-center justify-between border-t'>
          <span className='text-xs text-muted-foreground'>
            {value.length.toLocaleString()} 个字符
          </span>
          <SheetClose asChild>
            <Button type='button'>完成</Button>
          </SheetClose>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  )
}

const DEFAULT_PROFILE_FORM = {
  name: '',
  description: '用于检测账号在固定格式、指令遵循和完整输出方面是否出现异常。',
  model: '',
  system_prompt: '',
  prompt:
    '请用三点解释为什么天空呈蓝色，每点包含标题和一句说明，最后给出一句总结。',
  expected_text: '',
  expected_output: '',
  expected_image_url: '',
  max_output_tokens: 0,
  temperature: '',
  extra_body: '',
  enabled: true,
}

type ProfileFormState = typeof DEFAULT_PROFILE_FORM
type ProfileFormErrorKey =
  | 'name'
  | 'model'
  | 'prompt'
  | 'max_output_tokens'
  | 'temperature'
  | 'extra_body'
type ProfileFormErrors = Partial<Record<ProfileFormErrorKey, string>>

const PROFILE_ERROR_ORDER: ProfileFormErrorKey[] = [
  'name',
  'model',
  'prompt',
  'max_output_tokens',
  'temperature',
  'extra_body',
]

function profileForm(profile: ProbeProfile | null): ProfileFormState {
  if (!profile) return { ...DEFAULT_PROFILE_FORM }
  return {
    name: profile.name,
    description: profile.description,
    model: profile.model,
    system_prompt: profile.system_prompt,
    prompt: profile.prompt,
    expected_text: profile.expected_text,
    expected_output: profile.expected_output ?? '',
    expected_image_url: profile.expected_image_url,
    max_output_tokens: profile.max_output_tokens,
    temperature: profile.temperature == null ? '' : String(profile.temperature),
    extra_body: JSON.stringify(profile.extra_body ?? {}, null, 2),
    enabled: profile.enabled,
  }
}

function validateProfileForm({
  form,
  followUpstreamLimit,
  customTokenLimit,
}: {
  form: ProfileFormState
  followUpstreamLimit: boolean
  customTokenLimit: string
}): {
  errors: ProfileFormErrors
  body?: Record<string, unknown>
} {
  const errors: ProfileFormErrors = {}
  if (!form.name.trim()) errors.name = '请填写方案名称'
  if (!form.model.trim()) errors.model = '请填写上游模型名称'
  if (!form.prompt.trim()) errors.prompt = '请填写用于探测的测试提示词'

  const maxOutputTokens = followUpstreamLimit ? 0 : Number(customTokenLimit)
  if (
    !followUpstreamLimit &&
    (!Number.isInteger(maxOutputTokens) || maxOutputTokens < 1)
  ) {
    errors.max_output_tokens = '请输入大于 0 的整数，或启用“跟随上游”'
  }

  const temperature =
    form.temperature.trim() === '' ? null : Number(form.temperature)
  if (
    temperature != null &&
    (!Number.isFinite(temperature) || temperature < 0 || temperature > 2)
  ) {
    errors.temperature = 'Temperature 需要在 0 到 2 之间'
  }

  let extraBody: Record<string, unknown> = {}
  try {
    const parsed = JSON.parse(form.extra_body || '{}')
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      errors.extra_body = '附加请求体必须是 JSON 对象'
    } else {
      extraBody = parsed as Record<string, unknown>
    }
  } catch {
    errors.extra_body = 'JSON 格式有误，请检查括号、引号和逗号'
  }

  if (Object.keys(errors).length) return { errors }
  return {
    errors,
    body: {
      ...form,
      name: form.name.trim(),
      model: form.model.trim(),
      prompt: form.prompt.trim(),
      max_output_tokens: maxOutputTokens,
      temperature,
      extra_body: extraBody,
    },
  }
}

function Field({
  label,
  htmlFor,
  required = false,
  description,
  error,
  errorId,
  children,
  className = '',
}: {
  label: string
  htmlFor?: string
  required?: boolean
  description?: string
  error?: string
  errorId?: string
  children: React.ReactNode
  className?: string
}) {
  return (
    <div className={`grid gap-2 ${className}`}>
      <div>
        <label htmlFor={htmlFor} className='text-sm font-medium'>
          {label}
          {required && (
            <span className='ms-1 text-destructive' aria-hidden='true'>
              *
            </span>
          )}
        </label>
        {description && (
          <p className='mt-0.5 text-xs leading-5 text-muted-foreground'>
            {description}
          </p>
        )}
      </div>
      {children}
      {error && (
        <p
          id={errorId}
          role='alert'
          className='text-xs leading-5 text-destructive'
        >
          {error}
        </p>
      )}
    </div>
  )
}

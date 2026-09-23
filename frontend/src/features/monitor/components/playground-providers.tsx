import { useState, type Dispatch, type SetStateAction } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Check,
  CloudCog,
  Eye,
  KeyRound,
  Loader2,
  Pencil,
  Plus,
  RefreshCw,
  ServerCog,
  Star,
  Trash2,
} from 'lucide-react'
import { toast } from 'sonner'
import { api, type ChatProvider, type ChatProviderInput } from '@/lib/api'
import { cn, getErrorMessage } from '@/lib/utils'
import { EnabledBadge } from '@/components/enabled-badge'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Textarea } from '@/components/ui/textarea'
import { ActionToolbar, ToolbarAction } from '@/components/action-toolbar'
import { ConfirmDialog } from '@/components/confirm-dialog'
import { PasswordInput } from '@/components/password-input'
import { PlaygroundField } from './playground-field'
import { chatCompletionUrl } from './playground-support'

export function ProviderSettingsDialog({
  open,
  onOpenChange,
  providers,
  selectedProviderId,
  onProviderChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  providers: ChatProvider[]
  selectedProviderId: string
  onProviderChange: (
    providerId: string,
    providerOverride?: ChatProvider
  ) => void
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size='wide' className='overflow-hidden'>
        <DialogHeader className='shrink-0'>
          <DialogTitle className='flex items-center gap-2'>
            <ServerCog className='size-5 text-primary' />
            模型提供商
          </DialogTitle>
          <DialogDescription>
            管理聊天请求使用的接口、API Key 和模型列表。
          </DialogDescription>
        </DialogHeader>
        <div className='min-h-0 flex-1 overflow-y-auto pe-1'>
          <ProviderSettingsPanel
            providers={providers}
            selectedProviderId={selectedProviderId}
            onProviderChange={onProviderChange}
          />
        </div>
        <DialogFooter className='shrink-0'>
          <Button onClick={() => onOpenChange(false)}>完成</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

type ProviderDraft = {
  name: string
  baseUrl: string
  apiKey: string
  modelsText: string
  enabled: boolean
  isDefault: boolean
  clearApiKey: boolean
}

function ProviderSettingsPanel({
  providers,
  selectedProviderId,
  onProviderChange,
}: {
  providers: ChatProvider[]
  selectedProviderId: string
  onProviderChange: (
    providerId: string,
    providerOverride?: ChatProvider
  ) => void
}) {
  const queryClient = useQueryClient()
  const [editingId, setEditingId] = useState<string | 'new' | null>(null)
  const [deleteId, setDeleteId] = useState<string | null>(null)
  const [detailProviderId, setDetailProviderId] = useState<string | null>(null)
  const [draft, setDraft] = useState<ProviderDraft>(() => emptyProviderDraft())
  const [apiKeyVisible, setApiKeyVisible] = useState(false)
  const [revealingApiKey, setRevealingApiKey] = useState(false)
  const editingProvider =
    editingId && editingId !== 'new'
      ? providers.find((provider) => provider.id === editingId)
      : undefined
  const detailProvider = providers.find(
    (provider) => provider.id === detailProviderId
  )
  const deleteTarget = providers.find((provider) => provider.id === deleteId)

  const updateProviderCache = (provider: ChatProvider) => {
    queryClient.setQueryData<ChatProvider[]>(['chat-providers'], (current) =>
      sortChatProviders(
        [
          ...(current ?? []).filter((item) => item.id !== provider.id),
          provider,
        ].map((item) =>
          provider.isDefault && item.id !== provider.id
            ? { ...item, isDefault: false }
            : item
        )
      )
    )
  }

  const refreshProviderQueries = (providerId?: string) => {
    void queryClient.invalidateQueries({ queryKey: ['chat-providers'] })
    if (providerId)
      void queryClient.invalidateQueries({
        queryKey: ['chat-models', providerId],
      })
  }

  const saveProvider = useMutation({
    mutationFn: async () => {
      const name = draft.name.trim()
      const baseUrl = draft.baseUrl.trim()
      if (!name || !baseUrl) throw new Error('名称和 Base URL 为必填项')
      const body: ChatProviderInput = {
        name,
        baseUrl,
        apiKey: draft.apiKey.trim() || undefined,
        clearApiKey: draft.clearApiKey,
        models: parseModelNames(draft.modelsText),
        enabled: draft.enabled,
        isDefault: draft.isDefault,
      }
      return editingId === 'new'
        ? api.createChatProvider(body)
        : api.updateChatProvider(String(editingId), body)
    },
    onSuccess: (provider) => {
      const wasNew = editingId === 'new'
      updateProviderCache(provider)
      refreshProviderQueries(provider.id)
      if (
        provider.enabled &&
        (wasNew || provider.isDefault || provider.id === selectedProviderId)
      )
        onProviderChange(provider.id, provider)
      setEditingId(null)
      setDraft(emptyProviderDraft())
      setApiKeyVisible(false)
      toast.success(wasNew ? '模型提供商已创建' : '模型提供商已更新')
    },
    onError: (error) => toast.error(getErrorMessage(error)),
  })

  const syncModels = useMutation({
    mutationFn: api.syncChatProviderModels,
    onSuccess: (provider) => {
      updateProviderCache(provider)
      refreshProviderQueries(provider.id)
      toast.success(`已同步 ${provider.models.length} 个模型`)
    },
    onError: (error) => toast.error(getErrorMessage(error)),
  })

  const deleteProvider = useMutation({
    mutationFn: api.deleteChatProvider,
    onSuccess: (_value, providerId) => {
      queryClient.setQueryData<ChatProvider[]>(['chat-providers'], (current) =>
        current?.filter((provider) => provider.id !== providerId)
      )
      refreshProviderQueries(providerId)
      setDeleteId(null)
      if (editingId === providerId) setEditingId(null)
      if (detailProviderId === providerId) setDetailProviderId(null)
      toast.success('模型提供商已删除')
    },
    onError: (error) => toast.error(getErrorMessage(error)),
  })

  const startCreate = () => {
    setEditingId('new')
    setDeleteId(null)
    setDraft(emptyProviderDraft())
    setApiKeyVisible(false)
  }
  const startEdit = (provider: ChatProvider) => {
    setEditingId(provider.id)
    setDeleteId(null)
    setDraft(providerDraft(provider))
    setApiKeyVisible(false)
  }

  const changeApiKeyVisibility = (visible: boolean) => {
    if (!visible) {
      setApiKeyVisible(false)
      return
    }
    if (draft.apiKey) {
      setApiKeyVisible(true)
      return
    }
    const providerId = editingProvider?.id
    if (!providerId || !editingProvider.apiKeyConfigured) {
      setApiKeyVisible(true)
      return
    }
    setRevealingApiKey(true)
    void api
      .revealChatProviderApiKey(providerId)
      .then((result) => {
        setDraft((current) => ({
          ...current,
          apiKey: result.value,
          clearApiKey: false,
        }))
        setApiKeyVisible(true)
      })
      .catch((error) => toast.error(getErrorMessage(error)))
      .finally(() => setRevealingApiKey(false))
  }

  return (
    <div className='space-y-3 py-1'>
      <div className='flex items-center justify-between gap-3'>
        <span className='text-sm text-muted-foreground'>
          共 {providers.length} 个提供商
        </span>
        <div className='flex items-center gap-2'>
          <Button
            type='button'
            size='sm'
            variant='outline'
            onClick={() => refreshProviderQueries()}
          >
            <RefreshCw />
            刷新
          </Button>
          <Button type='button' size='sm' onClick={startCreate}>
            <Plus />
            新建
          </Button>
        </div>
      </div>

      <div className='overflow-hidden rounded-lg border'>
        <Table rememberRowKey='chat-providers'>
          <TableHeader>
            <TableRow>
              <TableHead>提供商</TableHead>
              <TableHead>状态</TableHead>
              <TableHead>API Key</TableHead>
              <TableHead>模型</TableHead>
              <TableHead className='text-right'>操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {providers.map((provider) => {
              const selected = selectedProviderId === provider.id
              const syncing =
                syncModels.isPending && syncModels.variables === provider.id
              return (
                <TableRow key={provider.id} rowId={provider.id}>
                  <TableCell className='min-w-64 whitespace-normal'>
                    <button
                      type='button'
                      className='block max-w-full text-left'
                      onClick={() => setDetailProviderId(provider.id)}
                    >
                      <span className='flex flex-wrap items-center gap-1.5 font-medium'>
                        {provider.name}
                        {provider.isDefault && (
                          <Badge variant='info'>
                            <Star className='size-3' />
                            默认
                          </Badge>
                        )}
                        {selected && <Badge variant='success'>当前</Badge>}
                      </span>
                      <span className='mt-1 block font-mono text-[11px] break-all text-muted-foreground'>
                        {provider.baseUrl}
                      </span>
                    </button>
                  </TableCell>
                  <TableCell>
                    <EnabledBadge enabled={provider.enabled} />
                  </TableCell>
                  <TableCell>
                    <span className='inline-flex items-center gap-1.5 text-xs text-muted-foreground'>
                      <KeyRound className='size-3.5' />
                      {provider.apiKeyConfigured ? '已配置' : '未配置'}
                    </span>
                  </TableCell>
                  <TableCell>{provider.models.length}</TableCell>
                  <TableCell>
                    <div className='flex justify-end'>
                      <ActionToolbar label={`${provider.name} 操作`}>
                        <ToolbarAction
                          label={`查看 ${provider.name}`}
                          onClick={() => setDetailProviderId(provider.id)}
                        >
                          <Eye />
                        </ToolbarAction>
                        <ToolbarAction
                          label={
                            selected ? '当前使用中' : `使用 ${provider.name}`
                          }
                          active={selected}
                          disabled={!provider.enabled || selected}
                          onClick={() =>
                            onProviderChange(provider.id, provider)
                          }
                        >
                          <Check />
                        </ToolbarAction>
                        <ToolbarAction
                          label={`同步 ${provider.name} 的模型`}
                          disabled={syncModels.isPending || !provider.enabled}
                          onClick={() => syncModels.mutate(provider.id)}
                        >
                          <RefreshCw
                            className={cn(syncing && 'animate-spin')}
                          />
                        </ToolbarAction>
                        <ToolbarAction
                          label={`编辑 ${provider.name}`}
                          onClick={() => startEdit(provider)}
                        >
                          <Pencil />
                        </ToolbarAction>
                        <ToolbarAction
                          label={`删除 ${provider.name}`}
                          destructive
                          onClick={() => setDeleteId(provider.id)}
                        >
                          <Trash2 />
                        </ToolbarAction>
                      </ActionToolbar>
                    </div>
                  </TableCell>
                </TableRow>
              )
            })}
            {!providers.length && (
              <TableRow>
                <TableCell
                  colSpan={5}
                  className='h-28 text-center text-muted-foreground'
                >
                  尚未配置模型提供商
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>

      <ProviderEditorDialog
        open={editingId !== null}
        onOpenChange={(open) => {
          if (!open) {
            setEditingId(null)
            setApiKeyVisible(false)
          }
        }}
        editingProvider={editingProvider}
        draft={draft}
        setDraft={setDraft}
        apiKeyVisible={apiKeyVisible}
        revealingApiKey={revealingApiKey}
        onApiKeyVisibleChange={changeApiKeyVisibility}
        pending={saveProvider.isPending}
        onSave={() => saveProvider.mutate()}
      />
      <ProviderDetailDialog
        provider={detailProvider}
        selected={detailProvider?.id === selectedProviderId}
        open={Boolean(detailProvider)}
        onOpenChange={(open) => !open && setDetailProviderId(null)}
        onUse={() => {
          if (!detailProvider?.enabled) return
          onProviderChange(detailProvider.id, detailProvider)
          setDetailProviderId(null)
        }}
        onEdit={() => {
          if (!detailProvider) return
          const provider = detailProvider
          setDetailProviderId(null)
          window.requestAnimationFrame(() => startEdit(provider))
        }}
      />
      <ConfirmDialog
        open={Boolean(deleteTarget)}
        onOpenChange={(open) => !open && setDeleteId(null)}
        title={`删除 ${deleteTarget?.name ?? '模型提供商'}？`}
        desc='删除后无法用于新请求，已保存在浏览器中的历史对话不会被移除。'
        confirmText={
          <>
            <Trash2 />
            删除
          </>
        }
        cancelBtnText='取消'
        destructive
        disabled={deleteProvider.isPending}
        handleConfirm={() =>
          deleteTarget && deleteProvider.mutate(deleteTarget.id)
        }
      />
    </div>
  )
}

function ProviderEditorDialog({
  open,
  onOpenChange,
  editingProvider,
  draft,
  setDraft,
  apiKeyVisible,
  revealingApiKey,
  onApiKeyVisibleChange,
  pending,
  onSave,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  editingProvider?: ChatProvider
  draft: ProviderDraft
  setDraft: Dispatch<SetStateAction<ProviderDraft>>
  apiKeyVisible: boolean
  revealingApiKey: boolean
  onApiKeyVisibleChange: (visible: boolean) => void
  pending: boolean
  onSave: () => void
}) {
  const followsSystemGateway =
    editingProvider?.name === '默认网关' && draft.name.trim() === '默认网关'
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className='flex items-center gap-2'>
            <CloudCog className='size-5 text-primary' />
            {editingProvider ? '编辑模型提供商' : '新建模型提供商'}
          </DialogTitle>
          <DialogDescription>
            Base URL 可填写服务根地址或以 /v1 结尾。
          </DialogDescription>
        </DialogHeader>
        <div className='grid gap-4 sm:grid-cols-2'>
          <PlaygroundField label='名称'>
            <Input
              value={draft.name}
              onChange={(event) =>
                setDraft((current) => ({
                  ...current,
                  name: event.target.value,
                }))
              }
              placeholder='例如：生产模型网关'
            />
          </PlaygroundField>
          <PlaygroundField label='Base URL'>
            <Input
              value={draft.baseUrl}
              readOnly={followsSystemGateway}
              onChange={(event) =>
                setDraft((current) => ({
                  ...current,
                  baseUrl: event.target.value,
                }))
              }
              placeholder='https://HOST/v1'
            />
            {followsSystemGateway && (
              <p className='text-xs text-muted-foreground'>
                默认网关跟随系统设置里的 grok2api
                服务地址。要连接其他服务，请新建提供商并改用其他名称。
              </p>
            )}
          </PlaygroundField>
          <PlaygroundField label='API Key' className='sm:col-span-2'>
            <PasswordInput
              value={draft.apiKey}
              visible={apiKeyVisible}
              onVisibleChange={onApiKeyVisibleChange}
              disabled={revealingApiKey}
              onChange={(event) =>
                setDraft((current) => ({
                  ...current,
                  apiKey: event.target.value,
                  clearApiKey: false,
                }))
              }
              placeholder={
                editingProvider?.apiKeyConfigured
                  ? '已配置；留空保持原值'
                  : '可选'
              }
            />
            {editingProvider?.apiKeyConfigured && (
              <label className='flex items-center gap-2 text-xs text-muted-foreground'>
                <Checkbox
                  checked={draft.clearApiKey}
                  onCheckedChange={(checked) =>
                    setDraft((current) => ({
                      ...current,
                      apiKey: '',
                      clearApiKey: checked === true,
                    }))
                  }
                />
                清除已保存的 API Key
              </label>
            )}
          </PlaygroundField>
          <PlaygroundField label='模型列表' className='sm:col-span-2'>
            <Textarea
              value={draft.modelsText}
              onChange={(event) =>
                setDraft((current) => ({
                  ...current,
                  modelsText: event.target.value,
                }))
              }
              className='min-h-36 font-mono text-xs'
              placeholder={'每行一个模型，也支持逗号分隔\nmodel-a\nmodel-b'}
              spellCheck={false}
            />
            <p className='text-xs text-muted-foreground'>
              留空时实时读取 /v1/models，也可在列表中手动同步。
            </p>
          </PlaygroundField>
          <div className='flex items-center justify-between py-1 text-sm'>
            <span>
              <span className='font-medium'>启用</span>
              <span className='block text-xs text-muted-foreground'>
                可在请求配置中选择
              </span>
            </span>
            <Switch
              checked={draft.enabled}
              onCheckedChange={(enabled) =>
                setDraft((current) => ({ ...current, enabled }))
              }
            />
          </div>
          <div className='flex items-center justify-between py-1 text-sm'>
            <span>
              <span className='font-medium'>设为默认</span>
              <span className='block text-xs text-muted-foreground'>
                新会话优先使用
              </span>
            </span>
            <Switch
              checked={draft.isDefault}
              onCheckedChange={(isDefault) =>
                setDraft((current) => ({
                  ...current,
                  isDefault,
                  enabled: isDefault ? true : current.enabled,
                }))
              }
            />
          </div>
        </div>
        <DialogFooter>
          <Button
            type='button'
            variant='outline'
            onClick={() => onOpenChange(false)}
          >
            取消
          </Button>
          <Button type='button' disabled={pending} onClick={onSave}>
            {pending && <Loader2 className='animate-spin' />}
            保存
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function emptyProviderDraft(): ProviderDraft {
  return {
    name: '',
    baseUrl: '',
    apiKey: '',
    modelsText: '',
    enabled: true,
    isDefault: false,
    clearApiKey: false,
  }
}

function providerDraft(provider: ChatProvider): ProviderDraft {
  return {
    name: provider.name,
    baseUrl: provider.baseUrl,
    apiKey: '',
    modelsText: provider.models.join('\n'),
    enabled: provider.enabled,
    isDefault: provider.isDefault,
    clearApiKey: false,
  }
}

function parseModelNames(value: string): string[] {
  return Array.from(
    new Set(
      value
        .split(/[\n,]/)
        .map((item) => item.trim())
        .filter(Boolean)
    )
  )
}

function sortChatProviders(values: ChatProvider[]): ChatProvider[] {
  return [...values].sort((left, right) => {
    if (left.isDefault !== right.isDefault) return left.isDefault ? -1 : 1
    return left.createdAt.localeCompare(right.createdAt)
  })
}

function ProviderDetailDialog({
  provider,
  selected,
  open,
  onOpenChange,
  onEdit,
  onUse,
}: {
  provider?: ChatProvider
  selected: boolean
  open: boolean
  onOpenChange: (open: boolean) => void
  onEdit: () => void
  onUse: () => void
}) {
  if (!provider) return null
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className='flex flex-wrap items-center gap-2'>
            <CloudCog className='size-5 text-primary' />
            {provider.name}
            {provider.isDefault && <Badge variant='info'>默认</Badge>}
            <EnabledBadge enabled={provider.enabled} />
            {selected && <Badge variant='success'>当前</Badge>}
          </DialogTitle>
          <DialogDescription>模型提供商配置详情</DialogDescription>
        </DialogHeader>
        <dl className='grid gap-x-4 gap-y-3 text-sm sm:grid-cols-[8rem_minmax(0,1fr)]'>
          <dt className='text-muted-foreground'>Base URL</dt>
          <dd className='font-mono text-xs break-all'>{provider.baseUrl}</dd>
          <dt className='text-muted-foreground'>Responses</dt>
          <dd className='font-mono text-xs break-all'>
            {chatCompletionUrl(provider.baseUrl)}
          </dd>
          <dt className='text-muted-foreground'>API Key</dt>
          <dd>{provider.apiKeyConfigured ? '已配置并加密保存' : '未配置'}</dd>
          <dt className='text-muted-foreground'>可用状态</dt>
          <dd>
            <EnabledBadge enabled={provider.enabled} />
          </dd>
        </dl>
        <div>
          <div className='mb-2 flex items-center justify-between gap-2'>
            <span className='text-sm font-medium'>模型列表</span>
            <Badge variant='outline'>{provider.models.length} 个</Badge>
          </div>
          {provider.models.length ? (
            <div className='flex max-h-52 flex-wrap gap-2 overflow-y-auto'>
              {provider.models.map((model) => (
                <Badge
                  key={model}
                  variant='secondary'
                  className='max-w-full font-mono font-normal'
                >
                  <span className='truncate'>{model}</span>
                </Badge>
              ))}
            </div>
          ) : (
            <p className='text-xs leading-5 text-muted-foreground'>
              未保存模型列表，可从 /v1/models 同步。
            </p>
          )}
        </div>
        <DialogFooter>
          <Button type='button' variant='outline' onClick={onEdit}>
            <Pencil />
            编辑
          </Button>
          <Button
            type='button'
            disabled={!provider.enabled || selected}
            onClick={onUse}
          >
            <Check />
            {selected ? '当前使用中' : '设为当前'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

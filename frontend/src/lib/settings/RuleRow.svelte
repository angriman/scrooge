<!--
@component
One rule: what it looks for, what it gives, and how many committed rows it matches. Where the
app can change it, it is edited in place, moved up or down in its file, moved between personal
and shared rules, or taken out.
-->
<script lang="ts">
	import ArrowDown from '@lucide/svelte/icons/arrow-down';
	import ArrowLeftRight from '@lucide/svelte/icons/arrow-left-right';
	import ArrowUp from '@lucide/svelte/icons/arrow-up';
	import Pencil from '@lucide/svelte/icons/pencil';
	import Trash from '@lucide/svelte/icons/trash-2';
	import { t } from '#lib/i18n/index.svelte.js';
	import CategorySelect from '#lib/import/CategorySelect.svelte';
	import type { Headed, Rule, RulesFile } from '#lib/types.js';
	import { changes, directionName, draftOf, fileTitle, matchesName, type Draft } from './rules.js';

	interface Props {
		rule: Rule;
		leaves: Headed[];
		targets: RulesFile[];
		editable: boolean;
		first: boolean;
		last: boolean;
		/** Each answers what went wrong, or null when it was done. */
		onsave: (draft: Draft) => Promise<string | null>;
		onmove: (by: -1 | 1) => Promise<string | null>;
		onmoveto: (to: string) => Promise<string | null>;
		onremove: () => Promise<string | null>;
		/** Try its pattern (as being edited) on the committed rows. */
		ontry: (match: string, direction: Rule['direction']) => void;
	}

	let {
		rule,
		leaves,
		targets,
		editable,
		first,
		last,
		onsave,
		onmove,
		onmoveto,
		onremove,
		ontry
	}: Props = $props();

	let mode = $state<'edit' | 'move' | null>(null);
	let draft = $state<Draft>({ match: '', category: '', direction: '' });
	let destination = $state('');
	let busy = $state(false);
	let problem = $state<string | null>(null);

	function edit() {
		draft = draftOf(rule);
		problem = null;
		mode = 'edit';
	}

	function startMove() {
		destination = targets.length === 1 ? targets[0].key : '';
		problem = null;
		mode = 'move';
	}

	async function act(change: () => Promise<string | null>) {
		busy = true;
		problem = await change();
		busy = false;
		if (!problem) mode = null;
	}

	async function save(event: SubmitEvent) {
		event.preventDefault();
		if (!changes(rule, draft)) {
			mode = null;
			return;
		}
		await act(() => onsave(draft));
	}

	async function moveTo(event: SubmitEvent) {
		event.preventDefault();
		if (!destination) return;
		await act(() => onmoveto(destination));
	}

	async function remove() {
		if (!confirm(t('settings.confirmRemoveRule', { match: rule.match, category: rule.category })))
			return;
		await act(onremove);
	}

	const field =
		'h-11 rounded-control border border-line bg-raised px-2 text-small text-ink focus:border-brand focus:outline-none sm:h-8';
	const icon =
		'inline-flex size-11 items-center justify-center rounded-control text-ink-secondary hover:bg-sunken hover:text-ink disabled:opacity-40 disabled:hover:bg-transparent sm:size-7';
	const tryButton =
		'inline-flex h-11 min-w-11 items-center justify-center rounded-control px-2 text-ink-secondary hover:bg-sunken hover:text-ink disabled:opacity-40 disabled:hover:bg-transparent sm:h-7 sm:min-w-7 sm:px-1.5';
</script>

<li class="py-2">
	{#if mode === 'edit'}
		<form onsubmit={save} class="flex flex-wrap items-end gap-2">
			<label class="flex min-w-0 flex-1 basis-60 flex-col gap-1 text-caption text-ink-secondary">
				{t('settings.patternLabel')}
				<input
					bind:value={draft.match}
					required
					maxlength={Math.max(200, rule.match.length)}
					spellcheck="false"
					class="{field} w-full font-mono"
				/>
			</label>
			<label class="flex flex-col gap-1 text-caption text-ink-secondary">
				{t('transactions.chipCategory')}
				<CategorySelect
					bind:value={draft.category}
					{leaves}
					label={`${t('transactions.chipCategory')}: ${rule.match}`}
				/>
			</label>
			<label class="flex flex-col gap-1 text-caption text-ink-secondary">
				{t('settings.moneyLabel')}
				<select bind:value={draft.direction} class={field}>
					<option value="">{t('settings.moneyBoth')}</option>
					<option value="in">{t('cashflow.inShort')}</option>
					<option value="out">{t('cashflow.outShort')}</option>
				</select>
			</label>
			<div class="flex gap-2">
				<button
					type="button"
					onclick={() => ontry(draft.match, draft.direction)}
					class="h-11 rounded-control border border-line px-3 text-small text-ink-secondary hover:text-ink sm:h-8"
				>
					{t('settings.try')}
				</button>
				<button
					type="button"
					onclick={() => (mode = null)}
					class="h-11 rounded-control border border-line px-3 text-small text-ink-secondary hover:text-ink sm:h-8"
				>
					{t('accounts.cancel')}
				</button>
				<button
					type="submit"
					disabled={busy || !draft.category}
					class="h-11 rounded-control bg-brand px-3 text-small font-medium text-on-brand hover:bg-brand-hover disabled:opacity-60 sm:h-8"
				>
					{busy ? t('settings.saving') : t('settings.save')}
				</button>
			</div>
		</form>
	{:else if mode === 'move'}
		<form onsubmit={moveTo} class="flex flex-wrap items-center gap-2">
			<span class="min-w-0 truncate text-small text-ink-secondary" title={rule.match}>
				{t('settings.moveRuleTo', { match: rule.match })}
			</span>
			<select
				bind:value={destination}
				aria-label={t('settings.moveRuleToAria', { match: rule.match })}
				class={field}
			>
				{#if targets.length > 1}
					<option value="">{t('settings.chooseRulePlace')}</option>
				{/if}
				{#each targets as target (target.key)}
					<option value={target.key}>{fileTitle(target)}</option>
				{/each}
			</select>
			<div class="flex gap-2">
				<button
					type="button"
					onclick={() => (mode = null)}
					class="h-11 rounded-control border border-line px-3 text-small text-ink-secondary hover:text-ink sm:h-8"
				>
					{t('accounts.cancel')}
				</button>
				<button
					type="submit"
					disabled={busy || !destination}
					class="h-11 rounded-control bg-brand px-3 text-small font-medium text-on-brand hover:bg-brand-hover disabled:opacity-60 sm:h-8"
				>
					{busy ? t('settings.saving') : t('settings.move')}
				</button>
			</div>
		</form>
	{:else}
		<div class="flex flex-col gap-1 sm:flex-row sm:items-center sm:gap-3">
			<div class="flex min-w-0 flex-1 items-baseline gap-3">
				<span class="w-6 shrink-0 text-right text-caption text-ink-muted tabular-nums"
					>{rule.position}</span
				>
				<div class="min-w-0 flex-1">
					<p class="flex min-w-0 flex-wrap items-baseline gap-x-2 text-body">
						<code class="min-w-0 truncate font-mono text-small text-ink" title={rule.match}
							>{rule.match}</code
						>
						<span class="text-ink-muted" aria-hidden="true">→</span>
						<span class="text-ink">{rule.category}</span>
					</p>
					<p class="text-caption text-ink-muted">
						{directionName(rule.direction)}{rule.account
							? ` ${t('settings.accountOnly', { account: rule.account })}`
							: ''} ·
						<span class={rule.matches === null ? 'text-caution' : ''}
							>{matchesName(rule.matches, rule.counted)}</span
						>
					</p>
					{#if editable && rule.fixed}
						<p class="text-caption text-caution">
							{t('settings.changeInFile', { file: rule.fixed })}
						</p>
					{/if}
				</div>
			</div>
			<div class="flex shrink-0 flex-wrap items-center gap-1 pl-7 sm:pl-0">
				<button
					type="button"
					class={tryButton}
					aria-label={t('settings.tryRuleAria', { match: rule.match })}
					title={t('settings.tryPatternTitle')}
					onclick={() => ontry(rule.match, rule.direction)}
				>
					<span class="text-caption">{t('settings.try')}</span>
				</button>
				{#if editable && !rule.fixed}
					<button
						type="button"
						class={icon}
						aria-label={t('settings.editRuleAria', { match: rule.match })}
						title={t('settings.edit')}
						onclick={edit}
					>
						<Pencil class="size-4" aria-hidden="true" />
					</button>
					{#if targets.length}
						<button
							type="button"
							class={icon}
							aria-label={t('settings.moveRuleAria', { match: rule.match })}
							title={t('settings.moveRuleTitle')}
							disabled={busy}
							onclick={startMove}
						>
							<ArrowLeftRight class="size-4" aria-hidden="true" />
						</button>
					{/if}
					<button
						type="button"
						class={icon}
						aria-label={t('settings.moveUpAria', { match: rule.match })}
						title={t('settings.triedEarlierTitle')}
						disabled={first || busy}
						onclick={() => act(() => onmove(-1))}
					>
						<ArrowUp class="size-4" aria-hidden="true" />
					</button>
					<button
						type="button"
						class={icon}
						aria-label={t('settings.moveDownAria', { match: rule.match })}
						title={t('settings.triedLaterTitle')}
						disabled={last || busy}
						onclick={() => act(() => onmove(1))}
					>
						<ArrowDown class="size-4" aria-hidden="true" />
					</button>
					<button
						type="button"
						class={icon}
						aria-label={t('settings.removeRuleAria', { match: rule.match })}
						title={t('accounts.remove')}
						disabled={busy}
						onclick={remove}
					>
						<Trash class="size-4" aria-hidden="true" />
					</button>
				{/if}
			</div>
		</div>
	{/if}
	{#if problem}<p class="mt-1 text-small text-loss" role="alert">{problem}</p>{/if}
</li>

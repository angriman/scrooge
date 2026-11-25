<script lang="ts">
	import CircleCheck from '@lucide/svelte/icons/circle-check';
	import { untrack } from 'svelte';
	import { invalidate } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { send } from '#lib/api.js';
	import { t } from '#lib/i18n/index.svelte.js';
	import AddCategory from '#lib/import/AddCategory.svelte';
	import DropZone from '#lib/import/DropZone.svelte';
	import RuleOffers from '#lib/import/RuleOffers.svelte';
	import StagedFiles from '#lib/import/StagedFiles.svelte';
	import StagedRows from '#lib/import/StagedRows.svelte';
	import UploadResult from '#lib/import/UploadResult.svelte';
	import Waiting from '#lib/import/Waiting.svelte';
	import { fresh, toSend, type GroupChoice } from '#lib/import/choices.js';
	import type { CategorizeResult, RuleSuggestion, Staged, Upload } from '#lib/types.js';
	import Amount from '#lib/ui/Amount.svelte';
	import Panel from '#lib/ui/Panel.svelte';
	import Segmented from '#lib/ui/Segmented.svelte';
	import { here, show } from '#lib/view.js';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const staged = $derived(data.staged);
	const token = $derived(data.session.token);
	const rows = $derived(new Map(staged.rows.map((row) => [row.id, row])));

	// What is chosen and not yet saved: kept while the draft changes under it, dropped for rows
	// that are no longer staged.
	let chosen = $state<Record<string, GroupChoice>>({});
	let own = $state<Record<string, string>>({});
	$effect.pre(() => {
		const [groups, rules, ids] = [staged.groups, staged.rules, rows];
		untrack(() => {
			chosen = fresh(groups, rules, chosen);
			own = Object.fromEntries(Object.entries(own).filter(([id]) => ids.has(id)));
		});
	});
	const pending = $derived(toSend(staged.groups, chosen, own, rows));
	// What stands in the way of committing, counting the categories chosen, which the commit
	// saves first.
	const blocked = $derived.by(() => {
		const given = new Set(pending.flatMap((choice) => choice.ids));
		const left = staged.rows.filter((row) => row.category === null && !given.has(row.id)).length;
		const waiting = t(left === 1 ? 'import.oneUncategorized' : 'import.manyUncategorized', {
			count: left
		});
		return [...staged.gaps, ...(left ? [waiting] : [])];
	});

	let answer = $state<Upload | null>(null);
	let uploading = $state(false);
	let looking = $state(false);
	let removing = $state<number | null>(null);
	let saving = $state<'save' | 'commit' | null>(null);
	let problems = $state<string[]>([]);
	let committed = $state<number | null>(null);
	let offers = $state<RuleSuggestion[]>([]);
	let unoffered = $state<string | null>(null);
	let adding = $state<string | null>(null);
	let added = $state<Record<string, boolean>>({});
	let offerErrors = $state<Record<string, string>>({});

	function clearOffers() {
		offers = [];
		unoffered = null;
		adding = null;
		added = {};
		offerErrors = {};
	}

	// What was just done is said for the person it was done for: a name, which changes only
	// when another person is chosen, not each time the draft is loaded again.
	const owner = $derived(staged.owner);
	$effect.pre(() => {
		void owner;
		untrack(() => {
			answer = null;
			committed = null;
			problems = [];
			clearOffers();
		});
	});

	async function upload(files: File[]) {
		const form = new FormData();
		form.set('owner', staged.owner);
		for (const file of files) form.append('file', file);
		uploading = true;
		committed = null;
		clearOffers();
		answer = (await send<Upload>('/api/import', form, token)).sent;
		uploading = false;
		await invalidate('api:import');
	}

	async function lookUp(held: string) {
		looking = true;
		answer = (await send<Upload>('/api/import/maturities', { held }, token)).sent;
		looking = false;
		await invalidate('api:import');
	}

	async function remove(file: Staged['files'][number]) {
		const asked = t(
			file.transactions === 1 ? 'accounts.confirmDiscardOne' : 'accounts.confirmDiscardMany',
			{ filename: file.filename, count: file.transactions }
		);
		if (!confirm(asked)) return;
		removing = file.id;
		const done = await send(`/api/statements/${file.id}/discard`, {}, token);
		problems = done.sent.errors ?? [];
		removing = null;
		await invalidate('api:import');
	}

	async function save(commit: boolean) {
		saving = commit ? 'commit' : 'save';
		problems = [];
		clearOffers();
		const done = await send<CategorizeResult>(
			'/api/import/categories',
			{ owner: staged.owner, choices: pending, commit },
			token
		);
		saving = null;
		if (done.ok) {
			own = {};
			chosen = {};
			committed = done.sent.committed;
			offers = done.sent.suggestions ?? [];
			unoffered = done.sent.unoffered ?? null;
			if (committed) answer = null;
		} else {
			problems = done.sent.errors ?? [];
		}
		await invalidate('api:import');
	}

	async function addRule(offer: RuleSuggestion) {
		adding = offer.id;
		offerErrors = { ...offerErrors, [offer.id]: '' };
		const done = await send<{ rule?: RuleSuggestion }>(
			'/api/import/rules',
			{ owner: staged.owner, id: offer.id },
			token
		);
		adding = null;
		if (done.ok) {
			added = { ...added, [offer.id]: true };
		} else {
			offerErrors = {
				...offerErrors,
				[offer.id]: (done.sent.errors ?? []).join(' ') || t('import.noRuleAdded')
			};
		}
	}

	async function addCategory(name: string, side: string, group: string) {
		const done = await send('/api/import/category', { name, side, group }, token);
		if (!done.ok) return (done.sent.errors ?? []).join(' ');
		await invalidate('api:import');
		return null;
	}

	const statements = (count: number) =>
		count === 1 ? t('import.oneStatement') : t('import.manyStatements', { count });
	const transactions = $derived(
		resolve('/transactions') +
			(page.url.searchParams.get('owner') ? `?owner=${encodeURIComponent(staged.owner)}` : '')
	);
</script>

<svelte:head>
	<title>{t('import.pageTitle')}</title>
</svelte:head>

<div class="mb-5 flex flex-wrap items-center justify-between gap-3">
	<h1 class="text-heading font-medium text-ink">{t('import.heading')}</h1>
	{#if staged.owners.length > 1}
		<Segmented
			label={t('import.whoseAria')}
			value={staged.owner}
			options={staged.owners.map((name) => ({ value: name, label: name }))}
			onchange={(name) => show(here(), { owner: name })}
		/>
	{/if}
</div>

{#if committed !== null}
	<p
		class="mb-4 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-panel border border-gain/30 bg-gain-soft px-4 py-2.5 text-body text-ink"
		role="status"
	>
		<CircleCheck class="size-4 text-gain" aria-hidden="true" />
		<span class="flex-1">
			{committed
				? t(committed === 1 ? 'import.oneCommitted' : 'import.manyCommitted', { count: committed })
				: t('import.nothingStagedToCommit')}
		</span>
		{#if committed}
			<a href={transactions} class="text-small font-medium text-brand hover:text-brand-hover"
				>{t('import.seeTransactions')}</a
			>
		{/if}
	</p>
{/if}

{#if offers.length || unoffered}
	<div class="mb-4">
		<RuleOffers {offers} {unoffered} {adding} {added} errors={offerErrors} onadd={addRule} />
	</div>
{/if}

<DropZone
	owner={staged.owners.length > 1 ? staged.owner : null}
	busy={uploading}
	compact={staged.files.length > 0}
	onfiles={upload}
/>

{#if answer}
	<div class="mt-4">
		<UploadResult {answer} owner={staged.owner} {looking} onlookup={lookUp} />
	</div>
{/if}

{#if !staged.files.length}
	<p class="mt-8 text-center text-body text-ink-secondary">
		{t('import.noneStagedFor', { owner: staged.owner })}
	</p>
{:else}
	<div
		class="mt-6 grid grid-cols-[minmax(0,1fr)] items-start gap-6 lg:grid-cols-[minmax(0,1fr)_24rem]"
	>
		<div class="space-y-6">
			{#if staged.groups.length}
				<Waiting
					groups={staged.groups}
					{rows}
					leaves={staged.choices.leaves}
					rules={staged.rules}
					bind:chosen
				/>
			{/if}

			<details class="rounded-panel bg-panel px-4 py-3" open={!staged.groups.length}>
				<summary class="cursor-pointer text-title font-medium text-ink">
					{t('import.everyStagedRow')}
					<span class="text-body font-normal text-ink-muted">{staged.rows.length}</span>
				</summary>
				<p class="mt-1 mb-2 text-small text-ink-secondary">
					{t('import.everyStagedRowNote')}
				</p>
				<StagedRows rows={staged.rows} leaves={staged.choices.leaves} bind:own />
			</details>

			{#if staged.rules}
				<AddCategory under={staged.under} onadd={addCategory} />
			{/if}
		</div>
		<div class="space-y-6 lg:sticky lg:top-20">
			<StagedFiles files={staged.files} {blocked} {removing} onremove={remove} />

			<div class="space-y-2">
				{#each problems as problem (problem)}
					<p class="text-small text-loss" role="alert">{problem}</p>
				{/each}
				<button
					type="button"
					onclick={() => save(true)}
					disabled={saving !== null}
					class="h-10 w-full rounded-control bg-brand px-4 text-body font-medium text-on-brand hover:bg-brand-hover disabled:opacity-60"
				>
					{saving === 'commit'
						? t('import.committing')
						: t('import.commitCta', { statements: statements(staged.files.length) })}
				</button>
				{#if pending.length}
					<button
						type="button"
						onclick={() => save(false)}
						disabled={saving !== null}
						class="h-9 w-full rounded-control border border-line px-4 text-body text-ink hover:border-line-strong disabled:opacity-60"
					>
						{t(saving === 'save' ? 'import.savingCategories' : 'import.saveCategoriesCta')}
					</button>
				{/if}
				<p class="text-caption text-ink-muted">
					{t('import.commitHint')}
				</p>
			</div>

			<Panel title={t('import.onceCommittedTitle')} note={t('import.onceCommittedNote')}>
				<ul class="space-y-1">
					{#each staged.totals as line (`${line.side}:${line.category}:${line.currency}`)}
						<li class="flex items-baseline justify-between gap-3 text-small">
							<span class="min-w-0 truncate {line.category ? 'text-ink' : 'text-caution'}">
								{line.category ?? t('import.waitingForCategory')}
								{#if line.side === 'transfer'}<span class="text-ink-muted"
										>{t('import.transferTag')}</span
									>{/if}
							</span>
							<span class="shrink-0 text-ink-secondary">
								<Amount value={line.total} currency={line.currency} class="text-ink" />
								<span class="ml-1 text-caption text-ink-muted">{line.count}</span>
							</span>
						</li>
					{/each}
				</ul>
			</Panel>
		</div>
	</div>
{/if}

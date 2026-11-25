<script lang="ts">
	import { resolve } from '$app/paths';
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import { send } from '#lib/api.js';
	import { day } from '#lib/format/date.js';
	import { t } from '#lib/i18n/index.svelte.js';
	import Notices from '#lib/overview/Notices.svelte';
	import Checks from '#lib/coverage/Checks.svelte';
	import Coverage from '#lib/coverage/Coverage.svelte';
	import { heldTogether } from '#lib/coverage/held.js';
	import type { StatementsAccount } from '#lib/types.js';
	import Amount from '#lib/ui/Amount.svelte';
	import type { PageProps } from './$types';

	type Statement = StatementsAccount['statements'][number];

	let { data }: PageProps = $props();

	const listed = $derived(data.listed);
	const owner = $derived(page.url.searchParams.get('owner'));
	const single = $derived(data.session.people.length < 2);
	const people = $derived(!single && !owner);
	const now = new Date();
	const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;

	// A closed account keeps its history but is listed apart, and is never behind.
	const opened = $derived(listed.accounts.filter((account) => !account.closed));
	const closed = $derived(listed.accounts.filter((account) => account.closed));
	const staged = $derived(opened.filter((account) => account.staged).length);

	let problem = $state<string | null>(null);
	// Taking a statement back stages it again; either way it then waits on the import page.
	async function remove(statement: Statement) {
		const asked = statement.committed
			? t('accounts.confirmUncommit', { filename: statement.filename })
			: t(
					statement.transactions === 1
						? 'accounts.confirmDiscardOne'
						: 'accounts.confirmDiscardMany',
					{ filename: statement.filename, count: statement.transactions }
				);
		if (!confirm(asked)) return;
		const action = statement.committed ? 'uncommit' : 'discard';
		const done = await send<{ owner: string }>(
			`/api/statements/${statement.id}/${action}`,
			{},
			data.session.token
		);
		if (!done.ok) {
			problem = (done.sent.errors ?? []).join(' ');
			return;
		}
		await goto(`${resolve('/import')}?owner=${encodeURIComponent(done.sent.owner)}`);
	}
	// A broker's currencies are closed and opened together: the API takes the name listed here.
	async function change(account: StatementsAccount, action: string, extra: object) {
		const done = await send(
			`/api/accounts/${action}`,
			{ owner: account.owner, account: account.account, ...extra },
			data.session.token
		);
		if (!done.ok) return (done.sent.errors ?? []).join(' ');
		await invalidateAll();
		return null;
	}
	const close = (account: StatementsAccount, when: string) =>
		change(account, 'close', { day: when });
	const reopen = (account: StatementsAccount) => change(account, 'reopen', {});
	const behind = $derived(opened.filter((account) => account.behind).length);
	const held = $derived(heldTogether(listed.accounts));
	const title = $derived(
		single
			? t('accounts.title')
			: owner
				? t('accounts.ownerTitle', { owner })
				: t('accounts.householdTitle')
	);
</script>

<svelte:head>
	<title>{t('accounts.pageTitle')}</title>
</svelte:head>

<Notices
	attention={data.attention}
	onePerson={single || owner !== null}
	readOnly={data.session.read_only}
/>

{#each listed.errors as error (error)}
	<p
		class="mb-4 flex items-start gap-2 rounded-panel border border-caution/40 bg-caution-soft px-4 py-3 text-body text-ink"
		role="status"
	>
		<CircleAlert class="mt-0.5 size-4 shrink-0 text-caution" aria-hidden="true" />
		{error}
	</p>
{/each}

{#if !listed.accounts.length}
	<section class="rounded-lead border border-line bg-panel px-6 py-16 text-center">
		<h1 class="text-heading font-medium text-ink">{t('overview.emptyHeading')}</h1>
		<p class="mx-auto mt-2 max-w-prose text-body text-ink-secondary">
			{t('accounts.emptyBody')}
		</p>
		<a
			href={resolve('/import')}
			class="mt-6 inline-flex rounded-control bg-brand px-4 py-2 text-body font-medium text-on-brand hover:bg-brand-hover"
		>
			{t('overview.emptyCta')}
		</a>
	</section>
{:else}
	<!-- The lead: what the accounts hold now, then up to when every statement of them is in. -->
	<section class="rounded-lead border border-line bg-panel px-5 py-5" aria-labelledby="accounts">
		<h1 id="accounts" class="text-small text-ink-secondary">{title}</h1>
		<p class="mt-1 text-figure font-medium tracking-tight text-ink sm:text-hero">
			{#if held.total !== null}
				<Amount value={held.total} currency={listed.currency} />
			{:else}
				<span class="text-figure text-ink-secondary">{t('accounts.noBalance')}</span>
			{/if}
		</p>
		<p class="mt-1 text-small text-ink-muted">
			{opened.length === 1
				? t('accounts.countOne')
				: t('accounts.countMany', { count: opened.length })}, {#if listed.covered}{t(
					'accounts.allInUpTo',
					{ date: day(listed.covered) }
				)}{:else}{t('accounts.nothingCommitted')}{/if}{#if behind},
				<span class="text-caution">{t('accounts.behindCount', { count: behind })}</span
				>{/if}{#if staged},
				<span class="text-brand">{t('accounts.stagedCount', { count: staged })}</span
				>{/if}{#if !behind && !staged}, {t('accounts.noneBehind')}{/if}.
		</p>
		{#if held.total !== null && held.unknown}
			<p class="mt-1 text-small text-ink-muted">
				{held.unknown === 1
					? t('accounts.unknownOne')
					: t('accounts.unknownMany', { count: held.unknown })}
			</p>
		{/if}
	</section>

	{#if problem}
		<p class="mt-4 text-body text-loss" role="alert">{problem}</p>
	{/if}
	{#if opened.length}
		<Coverage
			accounts={opened}
			currency={listed.currency}
			{today}
			{people}
			onremove={data.session.read_only ? undefined : remove}
			onclose={data.session.read_only ? undefined : close}
			onreopen={data.session.read_only ? undefined : reopen}
		/>
	{/if}
	{#if closed.length}
		<Coverage
			accounts={closed}
			currency={listed.currency}
			{today}
			{people}
			onremove={data.session.read_only ? undefined : remove}
			onclose={data.session.read_only ? undefined : close}
			onreopen={data.session.read_only ? undefined : reopen}
			title={closed.length === 1
				? t('accounts.closedOne')
				: t('accounts.closedMany', { count: closed.length })}
		/>
	{/if}

	{#if listed.checks.length}
		<div class="mt-10">
			<Checks checks={listed.checks} open={page.url.hash === '#checks'} {single} />
		</div>
	{/if}
{/if}

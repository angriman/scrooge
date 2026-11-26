<script lang="ts">
	import { resolve } from '$app/paths';
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import { errorMessage, send } from '#lib/api.js';
	import AccountDetails from '#lib/coverage/AccountDetails.svelte';
	import { accountKey, accountParam, findAccount } from '#lib/coverage/accounts.js';
	import Checks from '#lib/coverage/Checks.svelte';
	import Coverage from '#lib/coverage/Coverage.svelte';
	import { heldTogether } from '#lib/coverage/held.js';
	import TypedFigure from '#lib/coverage/TypedFigure.svelte';
	import { day, todayIso } from '#lib/format/date.js';
	import { t } from '#lib/i18n/index.svelte.js';
	import Notices from '#lib/overview/Notices.svelte';
	import type { StatementsAccount } from '#lib/types.js';
	import Amount from '#lib/ui/Amount.svelte';
	import Dialog from '#lib/ui/Dialog.svelte';
	import EmptyState from '#lib/ui/EmptyState.svelte';
	import SidePanel from '#lib/ui/SidePanel.svelte';
	import { detailOf, here, setDetail } from '#lib/view.js';
	import type { PageProps } from './$types';

	type Statement = StatementsAccount['statements'][number];

	let { data }: PageProps = $props();

	const listed = $derived(data.listed);
	const owner = $derived(page.url.searchParams.get('owner'));
	const single = $derived(data.session.people.length < 2);
	const people = $derived(!single && !owner);
	const today = todayIso();
	const canTypeAny = $derived(!data.session.read_only && data.session.typing.length > 0);

	// A closed account keeps its history but is listed apart, and is never behind.
	const opened = $derived(listed.accounts.filter((account) => !account.closed));
	const closed = $derived(listed.accounts.filter((account) => account.closed));
	const staged = $derived(opened.filter((account) => account.staged).length);

	const accountQuery = $derived(detailOf(here(), 'account'));
	const chosenAccount = $derived(findAccount(listed.accounts, accountQuery));
	const chosenKey = $derived(chosenAccount ? accountKey(chosenAccount) : null);

	// Remember the last shown account so focus returns to its row on close and its body stays
	// populated during the closing animation.
	let lastKey = $state<string | null>(null);
	let displayedAccount = $state<StatementsAccount | null>(null);
	$effect(() => {
		if (chosenAccount && chosenKey) {
			lastKey = chosenKey;
			displayedAccount = chosenAccount;
		}
	});
	const panelAccount = $derived(chosenAccount ?? displayedAccount);

	const rowButtons = new Map<string, HTMLButtonElement>();
	function registerRow(key: string, el: HTMLButtonElement | null) {
		if (el) rowButtons.set(key, el);
		else rowButtons.delete(key);
	}

	function chooseAccount(account: StatementsAccount) {
		const key = accountKey(account);
		void setDetail('account', chosenKey === key ? null : accountParam(account, listed.accounts));
	}

	function closePanel() {
		void setDetail('account', null);
	}

	function returnAccountFocus(): HTMLElement | null {
		return lastKey ? (rowButtons.get(lastKey) ?? null) : null;
	}

	let typingOpen = $state(false);
	let typingBusy = $state(false);
	let typeButtonRef = $state<HTMLButtonElement | null>(null);

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
			problem = errorMessage(done);
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
		if (!done.ok) return errorMessage(done);
		await invalidateAll();
		return null;
	}
	const close = (account: StatementsAccount, when: string) =>
		change(account, 'close', { day: when });
	const reopen = (account: StatementsAccount) => change(account, 'reopen', {});

	async function afterTyped() {
		await invalidateAll();
		const activeParam = detailOf(here(), 'account');
		if (activeParam && !findAccount(listed.accounts, activeParam)) {
			await setDetail('account', null);
		}
	}

	const behind = $derived(opened.filter((account) => account.behind).length);
	const held = $derived(heldTogether(listed.accounts));
	const title = $derived(
		single
			? t('accounts.title')
			: owner
				? t('accounts.ownerTitle', { owner })
				: t('accounts.householdTitle')
	);
	const typableAccountNames = $derived([
		...new Set(
			listed.accounts
				.filter((account) => account.typable !== 'none')
				.flatMap((account) => account.parts)
		)
	]);
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
	<EmptyState
		heading={t('overview.emptyHeading')}
		body={t('accounts.emptyBody')}
		importCta={!data.session.read_only}
	/>
	{#if canTypeAny}
		<div class="mt-4 flex justify-center">
			<button
				bind:this={typeButtonRef}
				type="button"
				onclick={() => (typingOpen = true)}
				class="h-8 rounded-control border border-line bg-panel px-3 text-small text-ink hover:border-line-strong hover:bg-raised"
			>
				{t('typed.button')}
			</button>
		</div>
	{/if}
{:else}
	<!-- The lead: what the accounts hold now, then up to when every statement of them is in. -->
	<section class="rounded-lead border border-line bg-panel px-5 py-5" aria-labelledby="accounts">
		<div class="flex flex-wrap items-start justify-between gap-3">
			<div>
				<h1 id="accounts" class="text-small text-ink-secondary">{title}</h1>
				<p class="mt-1 text-figure font-medium tracking-tight text-ink sm:text-hero">
					{#if held.total !== null}
						<Amount value={held.total} currency={listed.currency} />
					{:else}
						<span class="text-figure text-ink-secondary">{t('accounts.noBalance')}</span>
					{/if}
				</p>
			</div>
			{#if canTypeAny}
				<button
					bind:this={typeButtonRef}
					type="button"
					onclick={() => (typingOpen = true)}
					class="h-8 rounded-control border border-line px-3 text-small text-ink hover:border-line-strong hover:bg-raised"
				>
					{t('typed.button')}
				</button>
			{/if}
		</div>
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
			selected={chosenKey}
			onchoose={chooseAccount}
			{registerRow}
		/>
	{/if}
	{#if closed.length}
		<Coverage
			accounts={closed}
			currency={listed.currency}
			{today}
			{people}
			selected={chosenKey}
			onchoose={chooseAccount}
			{registerRow}
			title={closed.length === 1
				? t('accounts.closedOne')
				: t('accounts.closedMany', { count: closed.length })}
		/>
	{/if}

	<SidePanel
		open={Boolean(chosenAccount)}
		title={panelAccount ? panelAccount.account : ''}
		subject={chosenKey}
		onclose={closePanel}
		returnFocus={returnAccountFocus}
	>
		{#snippet meta()}
			{#if panelAccount}
				<span class="flex flex-wrap items-center gap-1.5">
					{#if people}
						<span class="text-ink-secondary">{panelAccount.owner}</span>
						<span aria-hidden="true">·</span>
					{/if}
					<span>{panelAccount.label || t('accounts.typedOnly')}</span>
					{#if panelAccount.closed}
						<span aria-hidden="true">·</span>
						<span>{t('accounts.closedOn', { date: day(panelAccount.closed) })}</span>
					{/if}
				</span>
			{/if}
		{/snippet}
		{#if panelAccount}
			{#key accountKey(panelAccount)}
				<AccountDetails
					account={panelAccount}
					currency={listed.currency}
					{today}
					currencies={data.session.currencies}
					token={data.session.read_only ? null : data.session.token}
					canType={!data.session.read_only && data.session.typing.includes(panelAccount.owner)}
					onremove={data.session.read_only ? undefined : remove}
					onclose={data.session.read_only ? undefined : (when) => close(panelAccount, when)}
					onreopen={data.session.read_only ? undefined : () => reopen(panelAccount)}
					ontyped={afterTyped}
				/>
			{/key}
		{/if}
	</SidePanel>

	{#if listed.checks.length}
		<div class="mt-10">
			<Checks checks={listed.checks} open={page.url.hash === '#checks'} {single} />
		</div>
	{/if}
{/if}

{#if canTypeAny}
	<Dialog
		bind:open={typingOpen}
		title={t('typed.title')}
		description={t('typed.note')}
		dismissible={!typingBusy}
		returnFocus={() => typeButtonRef}
	>
		<TypedFigure
			owners={data.session.typing}
			{owner}
			accounts={typableAccountNames}
			currencies={data.session.currencies}
			{today}
			token={data.session.token}
			ondone={afterTyped}
			onbusy={(state) => (typingBusy = state)}
		/>
	</Dialog>
{/if}

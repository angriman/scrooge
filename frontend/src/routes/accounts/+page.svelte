<script lang="ts">
	import { resolve } from '$app/paths';
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { send } from '#lib/api.js';
	import { day } from '#lib/format/date.js';
	import Notices from '#lib/overview/Notices.svelte';
	import Checks from '#lib/coverage/Checks.svelte';
	import Coverage from '#lib/coverage/Coverage.svelte';
	import type { StatementsAccount } from '#lib/types.js';
	import type { PageProps } from './$types';

	type Statement = StatementsAccount['statements'][number];

	let { data }: PageProps = $props();

	const listed = $derived(data.listed);
	const owner = $derived(page.url.searchParams.get('owner'));
	const single = $derived(data.session.people.length < 2);
	const people = $derived(!single && !owner);
	const now = new Date();
	const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;

	const staged = $derived(listed.accounts.filter((account) => account.staged).length);

	let problem = $state<string | null>(null);
	// Taking a statement back stages it again; either way it then waits on the import page.
	async function remove(statement: Statement) {
		const asked = statement.committed
			? `Take back ${statement.filename}? It is staged again, and not counted until it is committed again.`
			: `Remove ${statement.filename}? Its ${statement.transactions} rows go with it.`;
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
	const behind = $derived(listed.accounts.filter((account) => account.behind).length);
</script>

<svelte:head>
	<title>Accounts · scrooge</title>
</svelte:head>

<Notices attention={data.attention} onePerson={single || owner !== null} />

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
		<h1 class="text-heading font-medium text-ink">Import your first statements</h1>
		<p class="mx-auto mt-2 max-w-prose text-body text-ink-secondary">
			Each account shows here with how far its statements go.
		</p>
		<a
			href={resolve('/import')}
			class="mt-6 inline-flex rounded-control bg-brand px-4 py-2 text-body font-medium text-on-brand hover:bg-brand-hover"
		>
			Import statements
		</a>
	</section>
{:else}
	<!-- The lead: up to when every statement is in, said as a sentence, not a figure. -->
	<section class="rounded-lead border border-line bg-panel px-5 py-5" aria-labelledby="statements">
		<p id="statements" class="text-small text-ink-secondary">Statements</p>
		<h1 class="mt-1 text-figure font-medium tracking-tight text-ink sm:text-hero">
			{#if listed.covered}
				All in up to {day(listed.covered)}
			{:else}
				Nothing committed yet
			{/if}
		</h1>
		<p class="mt-2 text-body text-ink-secondary">
			{listed.accounts.length === 1 ? '1 account' : `${listed.accounts.length} accounts`}{#if behind},
				<span class="text-caution">{behind} behind</span>{/if}{#if staged},
				<span class="text-brand">{staged} with statements staged</span>{/if}{#if !behind && !staged},
				none behind and nothing staged{/if}.
		</p>
	</section>

	{#if problem}
		<p class="mt-4 text-body text-loss" role="alert">{problem}</p>
	{/if}
	<Coverage
		accounts={listed.accounts}
		currency={listed.currency}
		{today}
		{people}
		onremove={data.session.read_only ? undefined : remove}
	/>

	{#if listed.checks.length}
		<div class="mt-10">
			<Checks checks={listed.checks} open={page.url.hash === '#checks'} {single} />
		</div>
	{/if}
{/if}

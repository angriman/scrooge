<!--
@component
What waits for the reader before the figures can be trusted, one banner each: statements staged
and not yet counted, accounts whose statements stop well before the others, and statement
figures that differ from those worked out here. Nothing when nothing waits.
-->
<script lang="ts">
	import ChevronDown from '@lucide/svelte/icons/chevron-down';
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import Clock from '@lucide/svelte/icons/clock';
	import Inbox from '@lucide/svelte/icons/inbox';
	import Scale from '@lucide/svelte/icons/scale';
	import { resolve } from '$app/paths';
	import { day } from '#lib/format/date.js';
	import type { Attention } from '#lib/types.js';
	import Notice from '#lib/ui/Notice.svelte';
	import { here, pick } from '#lib/view.js';

	interface Props {
		attention: Attention;
		/** Whether the figures are of one person: their name then goes without saying. */
		onePerson: boolean;
	}

	let { attention, onePerson }: Props = $props();

	const importing = (owner: string) => `${resolve('/import')}?owner=${encodeURIComponent(owner)}`;
	// The checks are on the Statements page, for the same person and currency.
	const checks = $derived.by(() => {
		const query = pick(here(), ['owner', 'currency']).toString();
		return `${resolve('/statements')}${query ? `?${query}` : ''}#checks`;
	});
	const statements = (count: number) => (count === 1 ? '1 statement' : `${count} statements`);
	const count = $derived(
		attention.staged.length + attention.behind.length + (attention.differ ? 1 : 0)
	);
</script>

{#snippet notices()}
	{#each attention.staged as waiting (waiting.owner)}
		<Notice
			tone="waiting"
			icon={Inbox}
			action={{ label: 'Commit them', href: importing(waiting.owner) }}
		>
			{statements(waiting.count)}{onePerson ? '' : ` of ${waiting.owner}`}
			{waiting.count === 1 ? 'is' : 'are'} staged and not yet counted.
		</Notice>
	{/each}
	{#each attention.behind as account (account.owner + account.account)}
		<Notice
			tone="caution"
			icon={Clock}
			action={{ label: 'Import', href: importing(account.owner) }}
		>
			{onePerson ? '' : `${account.owner}’s `}{account.account} statements end on
			{account.committed ? day(account.committed) : 'no day yet'}, over a month before the others.
		</Notice>
	{/each}
	{#if attention.differ}
		<Notice tone="caution" icon={Scale} action={{ label: 'Review them', href: checks }}>
			{attention.differ === 1 ? '1 balance differs' : `${attention.differ} balances differ`} from
			what the statements say.
		</Notice>
	{/if}
{/snippet}

{#if count > 1}
	<!-- On a phone, several take one line until asked for: the figures come first. -->
	<details class="group mb-4 sm:hidden">
		<summary
			class="flex cursor-pointer list-none items-center gap-3 rounded-panel border border-caution/30 bg-caution-soft px-4 py-2.5 text-body text-ink"
		>
			<CircleAlert class="size-4 shrink-0 text-caution" aria-hidden="true" />
			<span class="flex-1">{count} things need a look</span>
			<ChevronDown class="size-4 text-ink-secondary group-open:rotate-180" aria-hidden="true" />
		</summary>
		<div class="mt-2 grid gap-2">{@render notices()}</div>
	</details>
	<div class="mb-4 hidden gap-2 sm:grid" role="status">{@render notices()}</div>
{:else if count === 1}
	<div class="mb-4 grid gap-2" role="status">{@render notices()}</div>
{/if}

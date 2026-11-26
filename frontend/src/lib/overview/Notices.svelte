<!--
@component
What needs a look, one line each: statements staged and not yet counted, accounts whose
statements stop well before the others, statement figures that differ from those worked out
here, rows still in the category of those no rule matched, and recurring payments that went
up or started lately. Beyond five lines the rest fold behind "N more". Nothing when nothing
waits.
-->
<script lang="ts">
	import ChevronDown from '@lucide/svelte/icons/chevron-down';
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import Clock from '@lucide/svelte/icons/clock';
	import Inbox from '@lucide/svelte/icons/inbox';
	import Repeat from '@lucide/svelte/icons/repeat';
	import Scale from '@lucide/svelte/icons/scale';
	import Tags from '@lucide/svelte/icons/tags';
	import TrendingUp from '@lucide/svelte/icons/trending-up';
	import { resolve } from '$app/paths';
	import { t } from '#lib/i18n/index.svelte.js';
	import { folded, noticeLines, type NoticeKind } from '#lib/overview/notices.js';
	import type { Attention } from '#lib/types.js';
	import Notice from '#lib/ui/Notice.svelte';
	import { here, pick } from '#lib/view.js';

	interface Props {
		attention: Attention;
		/** Whether the figures are of one person: their name then goes without saying. */
		onePerson: boolean;
		/** Whether this is a copy, where nothing can be changed. */
		readOnly: boolean;
	}

	let { attention, onePerson, readOnly }: Props = $props();

	const ICONS = {
		staged: Inbox,
		behind: Clock,
		differ: Scale,
		uncategorised: Tags,
		up: TrendingUp,
		new: Repeat
	} satisfies Record<NoticeKind, unknown>;

	const lines = $derived(
		noticeLines(
			attention,
			{ onePerson, readOnly },
			{
				importing: (owner) => `${resolve('/import')}?owner=${encodeURIComponent(owner)}`,
				// The checks are on the Accounts page, for the same person and currency.
				checks: (() => {
					const query = pick(here(), ['owner', 'currency']).toString();
					return `${resolve('/accounts')}${query ? `?${query}` : ''}#checks`;
				})(),
				// The rows, in the currency of the page: the person is the line's own.
				transactions: (fields) => {
					const query = new URLSearchParams(fields);
					const currency = here().searchParams.get('currency');
					if (currency) query.set('currency', currency);
					return `${resolve('/transactions')}?${query}`;
				}
			}
		)
	);
	let open = $state(false);
	const fold = $derived(open ? { shown: lines, more: 0 } : folded(lines));
</script>

{#snippet notices()}
	{#each fold.shown as line (line.key)}
		<Notice tone={line.tone} icon={ICONS[line.kind]} action={line.action}>{line.text}</Notice>
	{/each}
	{#if fold.more}
		<button
			type="button"
			class="inline-flex min-h-11 items-center justify-self-start px-1 text-small font-medium text-brand hover:text-brand-hover sm:min-h-0"
			onclick={() => (open = true)}
		>
			{t('notices.more', { count: fold.more })}
		</button>
	{/if}
{/snippet}

{#if lines.length > 1}
	<!-- On a phone, several take one line until asked for: the figures come first. -->
	<details class="group mb-4 sm:hidden">
		<summary
			class="flex min-h-11 cursor-pointer list-none items-center gap-3 rounded-panel border border-caution/30 bg-caution-soft px-4 py-2.5 text-body text-ink"
		>
			<CircleAlert class="size-4 shrink-0 text-caution" aria-hidden="true" />
			<span class="flex-1">{t('notices.thingsNeedALook', { count: lines.length })}</span>
			<ChevronDown class="size-4 text-ink-secondary group-open:rotate-180" aria-hidden="true" />
		</summary>
		<div class="mt-2 grid gap-2">{@render notices()}</div>
	</details>
	<section class="mb-4 hidden gap-2 sm:grid" role="status" aria-labelledby="needs-a-look">
		<h2 id="needs-a-look" class="text-small font-medium text-ink-secondary">
			{t('notices.needsALook')}
		</h2>
		{@render notices()}
	</section>
{:else if lines.length === 1}
	<div class="mb-4 grid gap-2" role="status">{@render notices()}</div>
{/if}

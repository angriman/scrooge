<script lang="ts">
	import '../app.css';
	import ArrowLeftRight from '@lucide/svelte/icons/arrow-left-right';
	import ChartPie from '@lucide/svelte/icons/chart-pie';
	import Files from '@lucide/svelte/icons/files';
	import House from '@lucide/svelte/icons/house';
	import ReceiptText from '@lucide/svelte/icons/receipt-text';
	import Upload from '@lucide/svelte/icons/upload';
	import { page } from '$app/state';
	import { resolve } from '$app/paths';
	import Choice from '#lib/ui/Choice.svelte';
	import Segmented from '#lib/ui/Segmented.svelte';
	import { cn } from '#lib/ui/cn.js';
	import { here, pick, show } from '#lib/view.js';
	import type { LayoutProps } from './$types';

	let { data, children }: LayoutProps = $props();

	const EVERYONE = '';

	const owner = $derived(page.url.searchParams.get('owner') ?? EVERYONE);
	const currency = $derived(page.url.searchParams.get('currency') ?? data.session.currency);
	const people = $derived([
		{ value: EVERYONE, label: 'Household' },
		...data.session.people.map((person) => ({ value: person.name, label: person.name }))
	]);

	// Between pages, whose figures and their currency go along; a page's own view does not.
	const along = $derived.by(() => {
		const query = pick(here(), ['owner', 'currency']).toString();
		return query ? `?${query}` : '';
	});
	const ours = (
		path: '/' | '/cashflow' | '/investments' | '/accounts' | '/transactions' | '/import'
	) => ({
		href: resolve(path) + along,
		current: page.url.pathname.replace(/\/$/, '') === resolve(path).replace(/\/$/, '')
	});
	const pages = $derived([
		{ label: 'Overview', icon: House, ...ours('/') },
		{ label: 'Cash flow', icon: ArrowLeftRight, ...ours('/cashflow') },
		{ label: 'Investments', icon: ChartPie, ...ours('/investments') },
		{ label: 'Accounts', icon: Files, ...ours('/accounts') },
		{ label: 'Transactions', icon: ReceiptText, ...ours('/transactions') }
	]);
</script>

<svelte:head>
	<title>scrooge</title>
</svelte:head>

<div class="min-h-dvh">
	<header class="sticky top-0 z-40 border-b border-line bg-page">
		<div
			class="mx-auto flex max-w-[1440px] flex-wrap items-center gap-x-6 gap-y-2 px-4 py-2 sm:px-6"
		>
			<a href={resolve('/')} class="flex items-center gap-2 text-title font-medium text-ink">
				<span class="size-3 rounded-[3px] bg-brand" aria-hidden="true"></span>
				scrooge
			</a>

			<nav aria-label="Pages" class="order-last -mx-1 flex w-full [scrollbar-width:none] overflow-x-auto md:order-none md:w-auto">
				{#each pages as item (item.label)}
					<a
						href={item.href}
						aria-current={item.current ? 'page' : undefined}
						class={cn(
							'relative flex items-center gap-1.5 px-2.5 py-2 text-body whitespace-nowrap text-ink-secondary hover:text-ink',
							item.current &&
								'text-ink after:absolute after:inset-x-2.5 after:-bottom-2 after:h-0.5 after:rounded-full after:bg-brand'
						)}
					>
						<item.icon class="size-4" aria-hidden="true" />
						{item.label}
					</a>
				{/each}
			</nav>

			<div class="ml-auto flex items-center gap-2">
				{#if data.session.people.length > 1}
					<Segmented
						label="Whose figures"
						value={owner}
						options={people}
						onchange={(chosen) => show(here(), { owner: chosen || null })}
					/>
				{/if}
				<Choice
					label="Currency"
					value={currency}
					options={data.session.currencies}
					onchange={(chosen) => show(here(), { currency: chosen })}
				/>
				{#if !data.session.read_only}
					<a
						href={ours('/import').href}
						aria-label="Import statements"
						class="inline-flex h-8 items-center gap-1.5 rounded-control bg-brand px-2.5 text-small font-medium text-on-brand hover:bg-brand-hover sm:px-3"
					>
						<Upload class="size-4" aria-hidden="true" />
						<span class="hidden sm:inline">Import</span>
					</a>
				{/if}
			</div>
		</div>
	</header>

	<main class="mx-auto max-w-[1440px] px-4 py-6 sm:px-6">
		{@render children()}
	</main>
</div>

<script lang="ts">
	import '../app.css';
	import ArrowLeftRight from '@lucide/svelte/icons/arrow-left-right';
	import ChartPie from '@lucide/svelte/icons/chart-pie';
	import Files from '@lucide/svelte/icons/files';
	import House from '@lucide/svelte/icons/house';
	import LogOut from '@lucide/svelte/icons/log-out';
	import ReceiptText from '@lucide/svelte/icons/receipt-text';
	import Settings from '@lucide/svelte/icons/settings';
	import Upload from '@lucide/svelte/icons/upload';
	import { page } from '$app/state';
	import { resolve } from '$app/paths';
	import { coveredUpTo } from '#lib/coverage/covered.js';
	import { day, dayOfYear } from '#lib/format/date.js';
	import Choice from '#lib/ui/Choice.svelte';
	import Segmented from '#lib/ui/Segmented.svelte';
	import { cn } from '#lib/ui/cn.js';
	import { here, pick, show } from '#lib/view.js';
	import type { LayoutProps } from './$types';
	import { beforeNavigate, goto, invalidateAll } from '$app/navigation';
	import { post } from '#lib/api.js';
	import { getLanguage, setLanguage, t, type Language } from '#lib/i18n/index.svelte.js';
	import { asked, stepTo } from '#lib/moved.js';
	import { renamed } from '#lib/renamed.js';

	let { data, children }: LayoutProps = $props();

	$effect(() => {
		setLanguage(data.session.language);
	});

	async function chooseLanguage(chosen: string) {
		const language = chosen.toLowerCase() as Language;
		if (language === getLanguage()) return;
		await post('/api/language', { language }, data.session.token);
		setLanguage(language);
		await invalidateAll();
	}

	// A step inside the app to a renamed page's old address goes to the new one before anything
	// loads, with the hash (`#checks`), which the old address's load cannot read. A link or form
	// keeps what it asks (replace, reset); what a `goto` asked is not told to `beforeNavigate`,
	// so that step to an old address is taken with goto's defaults.
	beforeNavigate((step) => {
		const moved = stepTo(step, location.origin, renamed);
		if (!moved) return;
		step.cancel();
		const from = step.type === 'link' || step.type === 'form' ? step.event.target : null;
		const element = from instanceof Element ? (from.closest('a') ?? from) : null;
		void goto(moved, asked(element));
	});

	const EVERYONE = '';

	const owner = $derived(page.url.searchParams.get('owner') ?? EVERYONE);
	const currency = $derived(page.url.searchParams.get('currency') ?? data.session.currency);
	// Up to when the statements of whose figures are shown are all in.
	const covered = $derived(coveredUpTo(data.session.people, owner));
	const people = $derived([
		{ value: EVERYONE, label: t('nav.household') },
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
		{ key: 'overview', label: t('nav.overview'), icon: House, ...ours('/') },
		{ key: 'cashflow', label: t('nav.cashflow'), icon: ArrowLeftRight, ...ours('/cashflow') },
		{ key: 'investments', label: t('nav.investments'), icon: ChartPie, ...ours('/investments') },
		{ key: 'accounts', label: t('nav.accounts'), icon: Files, ...ours('/accounts') },
		{
			key: 'transactions',
			label: t('nav.transactions'),
			icon: ReceiptText,
			...ours('/transactions')
		}
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

			<nav
				aria-label={t('nav.pages')}
				class="order-last -mx-1 flex w-full [scrollbar-width:none] overflow-x-auto md:order-none md:w-auto"
			>
				{#each pages as item (item.key)}
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

			<!-- Right before the switches, or on a phone at the end of the first line, by the name. -->
			{#if covered}
				<div class="flex-1 text-right">
					<a
						href={ours('/accounts').href}
						aria-label={t('nav.statementsCompleteLabel', { date: day(covered) })}
						title={t('nav.statementsCompleteTitle')}
						class="text-caption whitespace-nowrap text-ink-muted hover:text-ink-secondary"
					>
						<span class="hidden md:inline">{t('nav.completeUpTo', { date: day(covered) })}</span>
						<span class="md:hidden">{t('nav.upTo', { date: dayOfYear(covered) })}</span>
					</a>
				</div>
			{/if}

			<div class="ml-auto flex items-center gap-2">
				{#if data.session.people.length > 1}
					<Segmented
						label={t('nav.whoseFigures')}
						value={owner}
						options={people}
						onchange={(chosen) => show(here(), { owner: chosen || null })}
					/>
				{/if}
				<Choice
					label={t('nav.currency')}
					value={currency}
					options={data.session.currencies}
					onchange={(chosen) => show(here(), { currency: chosen })}
				/>
				<Choice
					label={t('nav.language')}
					value={getLanguage().toUpperCase()}
					options={data.session.languages.map((lang) => lang.toUpperCase())}
					onchange={chooseLanguage}
				/>
				<a
					href={resolve('/settings') + along}
					aria-label={t('nav.settingsLabel')}
					title={t('nav.settingsTitle')}
					aria-current={page.url.pathname.startsWith(resolve('/settings')) ? 'page' : undefined}
					class="inline-flex size-8 items-center justify-center rounded-control text-ink-secondary hover:bg-sunken hover:text-ink aria-[current=page]:text-brand"
				>
					<Settings class="size-4" aria-hidden="true" />
				</a>
				{#if !data.session.read_only}
					<a
						href={ours('/import').href}
						aria-label={t('nav.importLabel')}
						class="inline-flex h-8 items-center gap-1.5 rounded-control bg-brand px-2.5 text-small font-medium text-on-brand hover:bg-brand-hover sm:px-3"
					>
						<Upload class="size-4" aria-hidden="true" />
						<span class="hidden sm:inline">{t('nav.import')}</span>
					</a>
				{/if}
				{#if data.session.signing}
					<form method="post" action="/logout" class="inline-flex">
						<input type="hidden" name="token" value={data.session.token} />
						<button
							type="submit"
							aria-label={t('nav.signOut')}
							title={t('nav.signOut')}
							class="inline-flex size-8 items-center justify-center rounded-control text-ink-secondary hover:bg-sunken hover:text-ink"
						>
							<LogOut class="size-4" aria-hidden="true" />
						</button>
					</form>
				{/if}
			</div>
		</div>
	</header>

	<main class="mx-auto max-w-[1440px] px-4 py-6 sm:px-6">
		{@render children()}
	</main>
</div>

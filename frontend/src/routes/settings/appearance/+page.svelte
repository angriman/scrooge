<script lang="ts">
	import { t } from '#lib/i18n/index.svelte.js';
	import {
		getThemePreference,
		setThemePreference,
		type ThemePreference
	} from '#lib/theme.svelte.js';
	import Panel from '#lib/ui/Panel.svelte';
	import Segmented from '#lib/ui/Segmented.svelte';

	const themeOptions = $derived([
		{ value: 'system' as const, label: t('settings.themeSystem') },
		{ value: 'dark' as const, label: t('settings.themeDark') },
		{ value: 'light' as const, label: t('settings.themeLight') }
	]);

	function chooseTheme(chosen: ThemePreference) {
		setThemePreference(chosen);
	}
</script>

<svelte:head>
	<title>{t('settings.subpageTitle', { section: t('settings.sectionAppearance') })}</title>
</svelte:head>

<Panel title={t('settings.appearanceTitle')} note={t('settings.appearanceNote')}>
	<div class="flex flex-wrap items-center gap-3">
		<Segmented
			label={t('settings.themeLabel')}
			value={getThemePreference()}
			options={themeOptions}
			onchange={chooseTheme}
		/>
	</div>
	<p class="mt-4 text-caption text-ink-muted">{t('settings.themeHint')}</p>
</Panel>

import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ExternalSearchSourceChips from './ExternalSearchSourceChips.vue'

describe('ExternalSearchSourceChips', () => {
  it('固定显示真实站点图标，并保留南+未命中状态和搜索动作', async () => {
    const wrapper = mount(ExternalSearchSourceChips, {
      props: {
        item: {
          canonical_rjcode: 'RJ01576821',
          external_search: {
            anime_share: { status: 'loading', results: [], search_results: [] },
            south_plus: { status: 'miss', results: [], search_results: [] },
          },
        },
      },
    })

    const buttons = wrapper.findAll('.external-source-chip')
    expect(buttons).toHaveLength(3)
    expect(buttons[0].get('img').attributes('src')).toContain('anime-sharing')
    expect(buttons[1].get('img').attributes('src')).toContain('south-plus')
    expect(buttons[1].classes()).toContain('is-miss')
    expect(buttons[1].attributes('title')).toContain('未找到')

    await buttons[1].trigger('click')
    expect(wrapper.emitted('open')?.[0]?.[0]).toMatchObject({
      source: 'south_plus',
      status: 'miss',
    })
    expect(wrapper.emitted('open')?.[0]?.[0]?.results?.[0]?.url).toContain('keyword=RJ01576821')
  })

  it('asmr.one 标签复用 has_asmr_one 探测结果并跳转作品页', async () => {
    const wrapper = mount(ExternalSearchSourceChips, {
      props: {
        item: {
          canonical_rjcode: 'RJ01576821',
          has_asmr_one: true,
          asmr_available_rjcode: 'RJ01576822',
          external_search: {
            anime_share: { status: 'miss', results: [], search_results: [] },
            south_plus: { status: 'miss', results: [], search_results: [] },
          },
        },
      },
    })

    const buttons = wrapper.findAll('.external-source-chip')
    expect(buttons).toHaveLength(3)
    const asmrChip = buttons[2]
    expect(asmrChip.get('img').attributes('src')).toContain('asmr-one')
    expect(asmrChip.classes()).toContain('is-hit')
    expect(asmrChip.attributes('title')).toContain('命中')

    await asmrChip.trigger('click')
    const payload = wrapper.emitted('open')?.[0]?.[0]
    expect(payload).toMatchObject({ source: 'asmr_one', status: 'hit' })
    expect(payload.results[0].url).toBe('https://asmr.one/work/RJ01576822')
  })

  it('asmr.one 未命中时仍可跳转 canonical 作品页', async () => {
    const wrapper = mount(ExternalSearchSourceChips, {
      props: {
        item: {
          canonical_rjcode: 'RJ01576821',
          has_asmr_one: false,
          external_search: {
            anime_share: { status: 'miss', results: [], search_results: [] },
            south_plus: { status: 'miss', results: [], search_results: [] },
          },
        },
      },
    })

    const asmrChip = wrapper.findAll('.external-source-chip')[2]
    expect(asmrChip.classes()).toContain('is-miss')
    await asmrChip.trigger('click')
    const payload = wrapper.emitted('open')?.[0]?.[0]
    expect(payload.results[0].url).toBe('https://asmr.one/work/RJ01576821')
  })

  it('版本行模式：每个版本按自己的 RJ 展示独立三来源检索状态', async () => {
    const wrapper = mount(ExternalSearchSourceChips, {
      props: {
        item: {
          canonical_rjcode: 'RJ01576821',
          has_asmr_one: true,
          asmr_available_rjcode: 'RJ01576821',
          external_search: {
            anime_share: { status: 'hit', results: [], search_results: [] },
            south_plus: { status: 'hit', results: [], search_results: [] },
            variants: [
              {
                rjcode: 'RJ01576821',
                group_key: 'original',
                group_short_label: '原作',
                sources: {
                  anime_share: {
                    status: 'hit',
                    results: [{ source: 'anime_share', rjcode: 'RJ01576821', url: 'https://www.anime-sharing.com/threads/rj01576821/' }],
                    search_results: [],
                    search_url: '',
                  },
                  south_plus: { status: 'miss', results: [], search_results: [], search_url: '' },
                },
              },
              {
                rjcode: 'RJ01596605',
                group_key: 'simplified',
                group_short_label: '简中',
                sources: {
                  anime_share: {
                    status: 'miss',
                    results: [],
                    search_results: [{ source: 'anime_share', rjcode: 'RJ01596605', variant_key: 'simplified', variant_label: '简中', url: 'https://www.anime-sharing.com/search/?q=RJ01596605' }],
                    search_url: '',
                  },
                  south_plus: {
                    status: 'hit',
                    results: [{ source: 'south_plus', rjcode: 'RJ01596605', variant_key: 'simplified', variant_label: '简中', url: 'https://bbs.south-plus.net/read.php?tid=123' }],
                    search_results: [],
                    search_url: '',
                  },
                },
              },
            ],
          },
        },
        variant: { rjcode: 'RJ01596605', group_key: 'simplified', group_short_label: '简中' },
      },
    })

    const buttons = wrapper.findAll('.external-source-chip')
    expect(buttons).toHaveLength(3)

    // AnimeShare：本行简中未命中，点击走本行 RJ 的搜索页
    expect(buttons[0].classes()).toContain('is-miss')
    await buttons[0].trigger('click')
    expect(wrapper.emitted('open')?.[0]?.[0]?.results?.[0]?.url).toContain('RJ01596605')

    // 南+：本行简中命中
    expect(buttons[1].classes()).toContain('is-hit')
    await buttons[1].trigger('click')
    expect(wrapper.emitted('open')?.[1]?.[0]?.results?.[0]).toMatchObject({
      rjcode: 'RJ01596605',
      variant_key: 'simplified',
      variant_label: '简中',
    })

    // asmr.one：可用 RJ 是原版，本行简中按未命中展示并跳转本行作品页
    const asmrChip = buttons[2]
    expect(asmrChip.classes()).toContain('is-miss')
    await asmrChip.trigger('click')
    expect(wrapper.emitted('open')?.[2]?.[0]?.results?.[0]?.url).toBe('https://asmr.one/work/RJ01596605')
  })

  it('版本行模式：旧合并载荷按本行 RJ 过滤结果', async () => {
    const wrapper = mount(ExternalSearchSourceChips, {
      props: {
        item: {
          canonical_rjcode: 'RJ01576821',
          external_search: {
            anime_share: {
              status: 'hit',
              results: [
                { source: 'anime_share', rjcode: 'RJ01576821', url: 'https://www.anime-sharing.com/threads/rj01576821/' },
                { source: 'anime_share', rjcode: 'RJ01596605', url: 'https://www.anime-sharing.com/threads/rj01596605/' },
              ],
              search_results: [],
            },
            south_plus: { status: 'miss', results: [], search_results: [] },
          },
        },
        variant: { rjcode: 'RJ01596605', group_key: 'simplified', group_short_label: '简中' },
      },
    })

    const animeChip = wrapper.findAll('.external-source-chip')[0]
    expect(animeChip.classes()).toContain('is-hit')
    await animeChip.trigger('click')
    const results = wrapper.emitted('open')?.[0]?.[0]?.results || []
    expect(results).toHaveLength(1)
    expect(results[0].rjcode).toBe('RJ01596605')
  })

  it('版本行模式：英文版也按实际版本显示标签', async () => {
    const wrapper = mount(ExternalSearchSourceChips, {
      props: {
        item: {
          canonical_rjcode: 'RJ01576821',
          has_asmr_one: false,
          external_search: {
            anime_share: { status: 'miss', results: [], search_results: [] },
            south_plus: { status: 'miss', results: [], search_results: [] },
            variants: [{
              rjcode: 'RJ01576899',
              group_key: 'other',
              group_short_label: '英文',
              sources: {
                anime_share: { status: 'miss', results: [], search_results: [{ source: 'anime_share', rjcode: 'RJ01576899', variant_key: 'other', variant_label: '英文', url: 'https://www.anime-sharing.com/search/?q=RJ01576899' }], search_url: '' },
                south_plus: { status: 'miss', results: [], search_results: [{ source: 'south_plus', rjcode: 'RJ01576899', variant_key: 'other', variant_label: '英文', url: 'https://bbs.south-plus.net/search.php?keyword=RJ01576899' }], search_url: '' },
              },
            }],
          },
        },
        variant: { rjcode: 'RJ01576899', group_key: 'other', group_short_label: '英文' },
      },
    })

    const southChip = wrapper.findAll('.external-source-chip')[1]
    await southChip.trigger('click')
    const payload = wrapper.emitted('open')?.[0]?.[0]
    expect(payload.results[0]).toMatchObject({ variant_key: 'other', variant_label: '英文', rjcode: 'RJ01576899' })
  })
})

from intelligence.collectors import ChannelSpec
from intelligence.collectors.rss import RSSCollector


def test_engineering_channel_uses_official_category_feed_without_mixing_news():
    urls=[]
    def fetch(url, timeout):
        urls.append(url)
        return b'''<rss><channel>
        <item><guid>new-news</guid><title>Corporate news</title><link>https://example.com/news/one</link><category>Company</category></item>
        <item><guid>engineering</guid><title>Engineering method</title><link>https://example.com/index/two</link><category>Research</category><category>Engineering</category><description>Specific method</description></item>
        </channel></rss>'''
    channel=ChannelSpec('openai','engineering','blog','rss','https://example.com/news/engineering/',
        config={'feed_url':'https://example.com/news/rss.xml','include_categories':['Engineering']})
    page=RSSCollector(fetcher=fetch).collect(channel)
    assert urls==['https://example.com/news/rss.xml']
    assert [item.title for item in page.items]==['Engineering method']
    assert page.items[0].metadata['categories']==['Research','Engineering']
    assert page.next_cursor['last_external_id']=='new-news'

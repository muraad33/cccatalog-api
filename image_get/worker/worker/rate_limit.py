import asyncio
import aiohttp
import aredis
import tldextract
import random
import time
import logging as log
"""
Every TLD (e.g. flickr.com, metmuseum.org) gets a token bucket. Before a worker
crawls an image from a domain, it must acquire a token from the right bucket.
If there aren't enough tokens, the request will block until it has been
replenished.

When a new domain is discovered, the crawler will collect samples to determine a
baseline *time to first byte* (TTFB), which is used to measure whether we are
placing any strain on a server. Once a baseline has been established, we will
slowly ramp up the request rate until there is noticeable strain in the form of
an increase in TTFB or errors are returned (such as 429 Rate Limit Exceeded).

The crawler cluster uses a masterless model, meaning every worker will try to
manage the token buckets. Race conditions are prevented with distributed locks.
To minimize contention and blocking, we should lock optimistically whenever
feasible.
"""

# Prefix for keys tracking TLD rate limits
PREFIX = 'currtokens:'


class RateLimitedClientSession:
    """
    Wraps aiohttp.ClientSession and enforces rate limits.
    """
    def __init__(self, aioclient, redis):
        self.client = aioclient
        self.redis = redis

    async def _get_token(self, tld):
        """
        Get a rate limiting token for a URL.
        :param tld: The domain of the item.
        :return: whether a token was successfully obtained
        """
        token_key = f'{PREFIX}{tld.domain}.{tld.suffix}'
        tokens = int(await self.redis.decr(token_key))
        if tokens >= 0:
            token_acquired = True
        else:
            # Out of tokens
            await asyncio.sleep(random.uniform(0.01, 0.5))
            token_acquired = False
        return token_acquired

    async def get(self, url):
        tld = tldextract.extract(url)
        token_acquired = False
        while not token_acquired:
            token_acquired = await self._get_token(tld)
        return await self.client.get(url)
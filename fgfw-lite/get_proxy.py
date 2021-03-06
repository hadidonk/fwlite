#!/usr/bin/env python
# coding:utf-8
import base64
import random
import logging

from repoze.lru import lru_cache

from util import ip_to_country_code


ASIA = ('AE', 'AF', 'AL', 'AZ', 'BD', 'BH', 'BN', 'BT', 'CN', 'CY', 'HK', 'ID',
        'IL', 'IN', 'IQ', 'IR', 'JO', 'JP', 'KH', 'KP', 'KR', 'KW', 'KZ', 'LA',
        'LB', 'LU', 'MN', 'MO', 'MV', 'MY', 'NP', 'OM', 'PH', 'PK', 'QA', 'SA',
        'SG', 'SY', 'TH', 'TJ', 'TM', 'TW', 'UZ', 'VN', 'YE')
AFRICA = ('AO', 'BI', 'BJ', 'BW', 'CF', 'CG', 'CM', 'CV', 'DZ', 'EG', 'ET', 'GA', 'GH',
          'GM', 'GN', 'GQ', 'KE', 'LY', 'MA', 'MG', 'ML', 'MR', 'MU', 'MZ', 'NA', 'NE',
          'NG', 'RW', 'SD', 'SN', 'SO', 'TN', 'TZ', 'UG', 'ZA', 'ZM', 'ZR', 'ZW')
NA = ('BM', 'BS', 'CA', 'CR', 'CU', 'GD', 'GT', 'HN', 'HT', 'JM', 'MX', 'NI', 'PA', 'US', 'VE')
SA = ('AR', 'BO', 'BR', 'CL', 'CO', 'EC', 'GY', 'PE', 'PY', 'UY')
EU = ('AT', 'BE', 'BG', 'CH', 'CZ', 'DE', 'DK', 'EE', 'ES', 'FI', 'FR', 'GB',
      'GR', 'HR', 'HU', 'IE', 'IS', 'IT', 'LT', 'LV', 'MC', 'MD', 'MT', 'NL',
      'NO', 'PL', 'PT', 'RO', 'RU', 'SE', 'SK', 'SM', 'UA', 'UK', 'VA', 'YU')
PACIFIC = ('AU', 'CK', 'FJ', 'GU', 'NZ', 'PG', 'TO')

continent_list = [ASIA, AFRICA, NA, SA, EU, PACIFIC]


class get_proxy(object):
    """docstring for parent_proxy"""
    logger = logging.getLogger('get_proxy')
    logger.setLevel(logging.INFO)
    hdr = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s %(name)s:%(levelname)s %(message)s',
                                  datefmt='%H:%M:%S')
    hdr.setFormatter(formatter)
    logger.addHandler(hdr)

    def __init__(self, conf):
        self.conf = conf
        self.config()

    def config(self):
        from apfilter import ap_filter
        self.gfwlist = ap_filter()
        self.local = ap_filter()
        self.ignore = ap_filter()  # used by rules like "||twimg.com auto"

        for line in open('./fgfw-lite/local.txt'):
            rule, _, dest = line.strip().partition(' ')
            if dest:  # |http://www.google.com/url forcehttps
                self.add_redirect(rule, dest)
            else:
                self.add_rule(line, local=True)

        if self.conf.rproxy is False:
            self.logger.info('loading  gfwlist...')
            try:
                with open('./fgfw-lite/gfwlist.txt') as f:
                    data = f.read()
                    if '!' not in data:
                        data = ''.join(data.split())
                        data = base64.b64decode(data).decode()
                    for line in data.splitlines():
                        self.add_rule(line)
            except Exception:
                self.logger.warning('./fgfw-lite/gfwlist.txt is corrupted!')

    def redirect(self, hdlr):
        return self.conf.REDIRECTOR.redirect(hdlr)

    def add_redirect(self, rule, dest):
        return self.conf.REDIRECTOR.add_redirect(rule, dest, self)

    def bad302(self, uri):
        return self.conf.REDIRECTOR.bad302(uri)

    def add_ignore(self, rule):
        '''called by redirector'''
        from apfilter import ap_rule
        self.ignore.add(ap_rule(rule))

    def add_rule(self, line, local=False):
        try:
            apfilter = self.local if local else self.gfwlist
            apfilter.add(line)
        except ValueError as e:
            self.logger.debug('create autoproxy rule failed: %s' % e)

    @lru_cache(256, timeout=120)
    def ifhost_in_region(self, host, ip):
        try:
            code = ip_to_country_code(ip)
            if code in self.conf.region:
                self.logger.info('%s in %s' % (host, code))
                return True
            return False
        except Exception:
            pass

    def ifgfwed(self, uri, host, port, ip, level=1):
        if level == 0:
            return False

        if self.conf.rproxy:
            return None

        if int(ip) == 0:
            return True

        if ip.is_loopback:
            return False

        if level == 5:
            return True

        if ip.is_private:
            return False

        if level == 4:
            return True

        a = self.local.match(uri, host)
        if a is not None:
            return a

        if self.ignore.match(uri, host):
            return None

        if self.ifhost_in_region(host, str(ip)):
            return False

        if level == 2 and uri.startswith('http://'):
            return True

        if self.conf.HOSTS.get(host):
            return None

        if level == 3:
            return True

        if self.conf.userconf.dgetbool('fgfwproxy', 'gfwlist', True) and self.gfwlist.match(uri, host):
            return True

    def parentproxy(self, uri, host, command, ip, level=1):
        '''
            decide which parentproxy to use.
            url:  'www.google.com:443'
                  'http://www.inxian.com'
            host: ('www.google.com', 443) (no port number is allowed)
            level: 0 -- direct
                   1 -- auto:        proxy if local_rule, direct if ip in region or override, proxy if gfwlist
                   2 -- encrypt all: proxy if local_rule, direct if ip in region or override, proxy if gfwlist or not https
                   3 -- chnroute:    proxy if local_rule, direct if ip in region or override, proxy for all
                   4 -- global:      proxy if not local
                   5 -- global:      proxy if not localhost
        '''
        host, port = host

        ifgfwed = self.ifgfwed(uri, host, port, ip, level)

        if ifgfwed is False:
            if ip and ip.is_private:
                return [self.conf.parentlist.local or self.conf.parentlist.direct]
            return [self.conf.parentlist.direct]

        parentlist = list(self.conf.parentlist.httpsparents() if command == 'CONNECT' else self.conf.parentlist.httpparents())
        if len(parentlist) < self.conf.maxretry:
            parentlist.extend(parentlist[1:] if not ifgfwed else parentlist)
            parentlist = parentlist[:self.conf.maxretry]

        location = ip_to_country_code(ip) or u'None'

        def priority(parent):
            return parent.priority(command, host, location)

        if len(parentlist) > 1:
            random.shuffle(parentlist)
            parentlist = sorted(parentlist, key=priority)

        if ifgfwed:
            if not parentlist:
                self.logger.warning('No parent proxy available, direct connection is used')
                return [self.conf.parentlist.get('direct')]
        else:
            parentlist.insert(0, self.conf.parentlist.direct)

        if len(parentlist) == 1 and parentlist[0] is self.conf.parentlist.get('direct'):
            return parentlist

        if len(parentlist) > self.conf.maxretry:
            parentlist = parentlist[:self.conf.maxretry]
        return parentlist

    def notify(self, command, url, requesthost, success, failed_parents, current_parent, time=0):
        self.logger.debug('notify: %s %s %s, failed_parents: %r, final: %s' % (command, url, 'Success' if success else 'Failed', failed_parents, current_parent or 'None'))
        failed_parents = [k for k in failed_parents if 'pooled' not in k]
        if success:
            if 'direct' in failed_parents:
                if command == 'CONNECT':
                    rule = '|https://%s' % requesthost[0]
                else:
                    rule = '|http://%s' % requesthost[0] if requesthost[1] == 80 else '%s:%d' % requesthost
                if rule not in self.local.rules:
                    resp_time = self.conf.parentlist.get('direct').get_avg_resp_time(requesthost[0])
                    exp = pow(resp_time, 2.5) if resp_time > 1 else 1
                    self.add_temp(rule, min(exp, 60))
                    self.conf.stdout()

    def add_temp(self, rule, exp=None, quiet=False):
        # add temp rule for &exp minutes
        rule = rule.strip()
        if rule not in self.local.rules:
            self.local.add(rule, (exp * 60) if exp else None)
            self.logger.info('add autoproxy rule: %s%s' % (rule, (' expire in %.1f min' % exp) if exp else ''))

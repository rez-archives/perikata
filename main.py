#!/usr/bin/env python

import math, logging, urllib, random, Cookie
from datetime import datetime, timedelta

from google.appengine.ext import db
from google.appengine.api import urlfetch
from google.appengine.ext import webapp
from google.appengine.api.labs import taskqueue
#from google.appengine.api import memcache

from google.appengine.ext.webapp.util import run_wsgi_app
from django.utils import simplejson

from plurk_config import PLURK_API_KEY, PLURK_USERNAME, PLURK_PASSWORD, PATH_PREFIX

PLURK_COOKIES = dict()


class User(db.Model):
    name = db.StringProperty()
    idname = db.StringProperty()
    plurk_user_id = db.IntegerProperty(default=0)
    win_count = db.IntegerProperty(default=0)
    score = db.IntegerProperty(default=0)
    total_score = db.IntegerProperty(default=0)
    
class GameSession(db.Model):
    start_timestamp = db.DateTimeProperty()
    end_timestamp = db.DateTimeProperty()
    ended = db.BooleanProperty(default=False)
    count = db.IntegerProperty(default=0)
    
class Entry(db.Model):
    definition = db.StringProperty()
    phrase = db.StringProperty()
    played = db.BooleanProperty(default=False)
    ended = db.BooleanProperty(default=False)
    end_response_id = db.IntegerProperty(default=0)
    end_user = db.ReferenceProperty(User)
    end_timestamp = db.DateTimeProperty()
    end_score = db.IntegerProperty(default=0)
    plurk_id = db.IntegerProperty(default=0)
    start_timestamp = db.DateTimeProperty()
    delta_second = db.IntegerProperty(default=0)
    session = db.ReferenceProperty(GameSession)
    
    
class PlurkAPI:
    @classmethod
    def instance(cls):
        try:
            return cls._instance
        except:
            cls._instance = cls()
        return cls._instance
      
    def __init__(self):
        self.cookie = Cookie.SimpleCookie()
        self.logged_in = False

    def _login(self):
        url = "https://www.plurk.com/API/Users/login?api_key=%s&username=%s&password=%s" % (PLURK_API_KEY, PLURK_USERNAME, PLURK_PASSWORD)
        result = self._open( url )
        if result.status_code == 200:
            self.logged_in = True
            return True
        return False

    def open(self, url, data = None):
        if self.logged_in == False:
            self._login()
        return self._open(url, data)
      
    def _open(self, url, data=None):
        if data is None:
            method = urlfetch.GET
        else:
            method = urlfetch.POST

        while url is not None:
            response = urlfetch.fetch(
                url=url,
                payload=data,
                method=method,
                headers=self._getHeaders(self.cookie),
                allow_truncated=False,
                follow_redirects=False,
                deadline=10
                )
            data = None # Next request will be a get, so no need to send the data again. 
            method = urlfetch.GET
            self.cookie.load(response.headers.get('set-cookie', '')) # Load the cookies from the response
            url = response.headers.get('location')

        return response
    
    def _getHeaders(self, cookie):
        headers = {
            #'Host' : 'www.google.com',
            'User-Agent' : 'Mozilla/5.0 (Macintosh; U; Intel Mac OS X 10.5; en-US; rv:1.9.2.6) Gecko/20100625 Firefox/3.6.6',
            'Cookie' : self._makeCookieHeader(cookie)
            }
        return headers

    def _makeCookieHeader(self, cookie):
        cookieHeader = ""
        for value in cookie.values():
            cookieHeader += "%s=%s; " % (value.key, value.value)
        return cookieHeader

        
class FetchHandler(webapp.RequestHandler):
    def post(self):
        self.get()
    def get(self):
        url = "http://bahtera.org/kateglo/api.php?format=json&mod=random"
        result = urlfetch.fetch(url)
        phrase_count = 0
        if result.status_code == 200:
            #TODO: check for valid JSON (or catch the exceptions)
            msg = simplejson.loads(result.content)
            for p in msg['kateglo']:
                phrase = p['phrase']
                if ' ' in phrase:
                    continue
                if '-' in phrase:
                    continue
                if len(phrase) < 4 or len(phrase) > 20:
                    continue
                
                defs = []
                longest_len = 140
                longest_def = ''
                for d in p['definition']:
                    if len(d) > 4 and len(d) <= 110 and not phrase in d:
                        defs.append(d)
                        #if len(d) < longest_len:
                        #    longest_len = len(d)
                        #    longest_def = d
                if len(defs):
                    longest_def = random.choice(defs)
                if longest_def:
                    entry = Entry()
                    entry.definition = longest_def
                    entry.phrase = phrase
                    entry.put()
                    phrase_count += 1
                        
            self.response.out.write('Got %i new entries' % phrase_count)
        else:
            self.response.out.write('Error %i' % result.status_code)

class EmitHandler(webapp.RequestHandler):
    def post(self):
        self.get()
    def get(self):
        entry = Entry.all().filter('played =', False).get()
        if entry is None:
            self.response.out.write('Please fetch!')
            return
            
        gsess_key = self.request.get('session', '')
        gsess_ref = None
        if gsess_key:
            gsess_ref = GameSession.get(gsess_key)
            
        if gsess_ref is not None and gsess_ref.ended:
            self.response.out.write('Session ended')
            return
            
        msg = entry.phrase[0:2] +'-: '+ entry.definition
        
        plurkAPI = PlurkAPI.instance()
        url = "http://www.plurk.com/API/Timeline/plurkAdd?api_key=%s&content=%s&qualifier=%s&lang=id" % (PLURK_API_KEY, urllib.quote(msg), 'says')
        result = plurkAPI.open(url)
        if result.status_code == 200:
            self.response.out.write('%r' % result.content)
            #TODO: check for valid JSON (or catch the exceptions)
            msg = simplejson.loads(result.content)
            entry.plurk_id = int(msg['plurk_id'])
            entry.played = True
            if gsess_ref is not None:
                entry.session = gsess_ref
                gsess_ref.count += 1
                gsess_ref.put()
            entry.start_timestamp = datetime.utcnow()
            
            entry.put()
            
            taskqueue.add(name='plurk-checker-%i-%i' % (entry.plurk_id, 1), countdown=10, url=PATH_PREFIX +'/check', params={'id': entry.plurk_id, 'task_counter': 1})

        else:
            self.response.out.write('Error %i<br/>%s' % (result.status_code, result.content))

class CheckHandler(webapp.RequestHandler):
    def post(self):
        self.get()
        
    def get(self):
        plurk_id = self.request.get('id')
        if not plurk_id:
            self.response.out.write('No ID')
            return
        plurk_id = int(plurk_id)
        
        plurkAPI = PlurkAPI.instance()
        url = "http://www.plurk.com/API/Responses/get?api_key=%s&from_response=0&plurk_id=%i" % (PLURK_API_KEY, plurk_id)
        result = plurkAPI.open(url)
        if result.status_code == 200:
            tnow = datetime.utcnow()
            score = 0
            ended = False
            user_ref = None
            entry = Entry.all().filter('plurk_id =', plurk_id).get()
            if entry is None:
                return
            if entry.ended:
                self.response.out.write('Already ended')
                return
            #TODO: checks
            self.response.out.write('%s' % result.content)
            #TODO: check for valid JSON (or catch the exceptions)
            msg = simplejson.loads(result.content)
            resp_count = len(msg['responses'])
            for pp in msg['responses']:
                cwl = pp['content'].lower().split()
                if entry.phrase.lower() in cwl:
                    dt = tnow - entry.start_timestamp
                    score = int(math.floor(dt.seconds * 0.05)) + 1
                    if score > 50:
                        score = 50
                    
                    user_id = int(pp['user_id'])
                    user_name = msg['friends'][str(user_id)]['nick_name']
                    user_ref = User.all().filter('plurk_user_id =', user_id).get()
                    if user_ref is None:
                        user_ref = User.all().filter('idname =', user_name.lower()).get()
                        if user_ref is not None:
                            user_ref.plurk_user_id = user_id
                    if user_ref is None:
                        user_ref = User()
                        user_ref.name = user_name
                        user_ref.idname = user_ref.name.lower()
                        user_ref.plurk_user_id = user_id
                        user_ref.win_count = 0
                    user_ref.win_count += 1
                    if not hasattr(user_ref, 'score'):
                        user_ref.score = 0
                    user_ref.score += score
                    user_ref.put()
                    
                    entry.ended = True
                    entry.end_response_id = int(pp['id'])
                    entry.end_user = user_ref
                    entry.end_timestamp = tnow
                    entry.end_score = score
                    entry.delta_second = dt.seconds
                    entry.put()
                    
                    ended = True
                    
                    self.response.out.write('**OK!**')
                    break
                
            if ended:
                message = '@'+ user_ref.name +' betul. Jawabannya adalah **'+ entry.phrase +'**. +'+ str(score) +' poin. @'+ user_ref.name +' total '+ str(user_ref.score) +' poin.'
                url = "http://www.plurk.com/API/Responses/responseAdd?api_key=%s&plurk_id=%i&content=%s&qualifier=%s&lang=id" % (PLURK_API_KEY, entry.plurk_id, urllib.quote(message), 'says')
                result = plurkAPI.open(url)
                if hasattr(entry, 'session') and entry.session is not None:
                    if entry.session.count < 20:
                        taskqueue.add(countdown=20, url=PATH_PREFIX +'/emit', params={'session': str(entry.session.key())})
                    elif not entry.session.ended:
                        entry.session.end_timestamp = datetime.utcnow()
                        entry.session.ended = True
                        entry.session.put()
                        sess_entry_list = Entry.all().filter('session =', entry.session)
                        highest_user = None
                        highest_pt = 0
                        wpt = {}
                        for se in sess_entry_list:
                            if hasattr(se, 'end_user') and se.end_user is not None:
                                if wpt.get(str(se.end_user.key())):
                                    wpt[str(se.end_user.key())] += se.end_score
                                else:
                                    wpt[str(se.end_user.key())] = se.end_score
                                if wpt[str(se.end_user.key())] > highest_pt:
                                    highest_user = se.end_user
                                    highest_pt = wpt[str(se.end_user.key())]
                        pmsg = '**Sesi ditutup** Sesi dimenangkan oleh @%s yang dalam sesi ini mengumpulkan %i poin.' % (highest_user.name, highest_pt)
                        url = "http://www.plurk.com/API/Timeline/plurkAdd?api_key=%s&content=%s&qualifier=%s&lang=id" % (PLURK_API_KEY, urllib.quote(pmsg), ':')
                        result = plurkAPI.open(url)
                        if result.status_code == 200:
                            pass
            else:
                if resp_count and tnow - entry.start_timestamp > timedelta(minutes=15):
                    message = 'Pertanyaan ditutup. Jawabannya adalah **'+ entry.phrase +'**.'
                    url = "http://www.plurk.com/API/Responses/responseAdd?api_key=%s&plurk_id=%i&content=%s&qualifier=%s&lang=id" % (PLURK_API_KEY, entry.plurk_id, urllib.quote(message), 'says')
                    result = plurkAPI.open(url)
                    entry.ended = True
                    entry.put()
                else:
                    task_counter = int(self.request.get('task_counter', 0)) + 1
                    taskqueue.add(name='plurk-checker-%i-%i' % (entry.plurk_id, task_counter), countdown=10, url=PATH_PREFIX +'/check', params={'id': entry.plurk_id, 'task_counter': task_counter})
        else:
            self.response.out.write('Error %i<pre>\n%s\n</pre>' % (result.status_code, result.content))
            
            
class FinishHandler(webapp.RequestHandler):
    def get(self):
        plurk_id = self.request.get('id')
        if not plurk_id:
            self.response.out.write('No ID')
            return
        plurk_id = int(plurk_id)
        
        ended = False
        user_ref = None
        entry = Entry.all().filter('plurk_id =', plurk_id).get()
        if entry.ended:
            self.response.out.write('Already ended')
            return
            
        plurkAPI = PlurkAPI.instance()
        message = 'Pertanyaan ditutup. Jawabannya adalah **'+ entry.phrase +'**.'
        url = "http://www.plurk.com/API/Responses/responseAdd?api_key=%s&plurk_id=%i&content=%s&qualifier=%s&lang=id" % (PLURK_API_KEY, entry.plurk_id, urllib.quote(message), 'says')
        result = plurkAPI.open(url)
            
        entry.ended = True
        entry.put()
        
        self.response.out.write('Done');


class ActiveListHandler(webapp.RequestHandler):
    def get(self):
        entry_list = Entry.all().filter('played =', True).filter('ended =', False).fetch(100)
        for e in entry_list:
            self.response.out.write('%i: %s - %s<br/>' % (e.plurk_id, e.phrase, e.definition))
            
class EnsureSupplyHandler(webapp.RequestHandler):
    def get(self):
        entry_list = Entry.all().filter('played =', False).fetch(100)
        if len(entry_list) < 20:
            self.response.out.write('Queue fetch')
            taskqueue.add(countdown=10, url=PATH_PREFIX +'/fetch')
class EnsureActiveHandler(webapp.RequestHandler):
    def get(self):
        active_list = Entry.all().filter('played =', True).filter('ended =', False).fetch(100)
        if len(active_list) == 0:
            self.response.out.write('Queue active')
            taskqueue.add(countdown=16, url=PATH_PREFIX +'/emit')
            taskqueue.add(countdown=18, url=PATH_PREFIX +'/emit')
            taskqueue.add(countdown=20, url=PATH_PREFIX +'/emit')
            
class GameOnHandler(webapp.RequestHandler):
    def get(self):
        #TODO: end all
        gsess_ref = GameSession()
        gsess_ref.start_timestamp = datetime.utcnow()
        gsess_ref.ended = False
        gsess_ref.put()
        plurkAPI = PlurkAPI.instance()
        pmsg = '**Sesi selanjutnya akan dimulai dalam waktu 300 detik**'
        url = "http://www.plurk.com/API/Timeline/plurkAdd?api_key=%s&content=%s&qualifier=%s&lang=id" % (PLURK_API_KEY, urllib.quote(pmsg), ':')
        result = plurkAPI.open(url)
        if result.status_code == 200:
            pass
        taskqueue.add(countdown=300, url=PATH_PREFIX +'/emit', params={'session': str(gsess_ref.key())})
        taskqueue.add(countdown=302, url=PATH_PREFIX +'/emit', params={'session': str(gsess_ref.key())})


class LeaderBoardHandler(webapp.RequestHandler):
    def get(self):
        ulist = User.all().order('-score').fetch(5)
        msg = 'Poin:'
        for u in ulist:
            if not hasattr(u, 'score') or not u.score:
                u.score = u.win_count
                u.put()
            msg += ' @%s (%i)' % (u.name, u.score)
            
        do_plurk = self.request.get('emit', False)
        if do_plurk:
            plurkAPI = PlurkAPI.instance()
            url = "http://www.plurk.com/API/Timeline/plurkAdd?api_key=%s&content=%s&qualifier=%s&lang=id" % (PLURK_API_KEY, urllib.quote(msg), 'shares')
            result = plurkAPI.open(url)
            if result.status_code == 200:
                self.response.out.write('OK')
        self.response.out.write(msg)

class ResetScoreHandler(webapp.RequestHandler):
    def get(self):
        i = 0
        ulist = User.all().order('-score').fetch(100) #TODO: all
        msg = '**Reset Poin** Peringkat akhir:'
        for u in ulist:
            i += 1
            if i <= 5:
                msg += ' @%s (%i)' % (u.name, u.score)
            if not hasattr(u, 'total_score'):
                u.total_score = u.score
            else:
                u.total_score += u.score
            u.score = 0
            u.put()
        
        plurkAPI = PlurkAPI.instance()
        url = "http://www.plurk.com/API/Timeline/plurkAdd?api_key=%s&content=%s&qualifier=%s&lang=id" % (PLURK_API_KEY, urllib.quote(msg), 'shares')
        result = plurkAPI.open(url)
        if result.status_code == 200:
            self.response.out.write('OK')
        self.response.out.write(msg)


application = webapp.WSGIApplication([
    (PATH_PREFIX +'/fetch', FetchHandler),
    (PATH_PREFIX +'/emit', EmitHandler),
    (PATH_PREFIX +'/check', CheckHandler),
    (PATH_PREFIX +'/finish', FinishHandler),
    (PATH_PREFIX +'/active_list', ActiveListHandler),
    (PATH_PREFIX +'/leader_board', LeaderBoardHandler),
    (PATH_PREFIX +'/reset_score', ResetScoreHandler),
    (PATH_PREFIX +'/ensure_active', EnsureActiveHandler),
    (PATH_PREFIX +'/ensure_supply', EnsureSupplyHandler),
    (PATH_PREFIX +'/game_on', GameOnHandler),
    ], debug=False)

def main():
    run_wsgi_app(application)

if __name__ == "__main__":
    main()

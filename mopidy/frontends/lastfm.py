from httplib import HTTPException
import logging
import multiprocessing
import socket
import time
from xml.parsers.expat import ExpatError

try:
    import pylast
except ImportError as import_error:
    from mopidy import OptionalDependencyError
    raise OptionalDependencyError(import_error)

from mopidy import settings, SettingsError
from mopidy.frontends.base import BaseFrontend
from mopidy.utils.process import BaseThread

logger = logging.getLogger('mopidy.frontends.lastfm')

API_KEY = '2236babefa8ebb3d93ea467560d00d04'
API_SECRET = '94d9a09c0cd5be955c4afaeaffcaefcd'

class LastfmFrontend(BaseFrontend):
    """
    Frontend which scrobbles the music you play to your `Last.fm
    <http://www.last.fm>`_ profile.

    .. note::

        This frontend requires a free user account at Last.fm.

    **Dependencies:**

    - `pylast <http://code.google.com/p/pylast/>`_ >= 0.5

    **Settings:**

    - :attr:`mopidy.settings.LASTFM_USERNAME`
    - :attr:`mopidy.settings.LASTFM_PASSWORD`
    """

    def __init__(self, *args, **kwargs):
        super(LastfmFrontend, self).__init__(*args, **kwargs)
        (self.connection, other_end) = multiprocessing.Pipe()
        self.thread = LastfmFrontendThread(self.core_queue, other_end)

    def start(self):
        self.thread.start()

    def destroy(self):
        self.thread.destroy()

    def process_message(self, message):
        if self.thread.is_alive():
            self.connection.send(message)


class LastfmFrontendThread(BaseThread):
    # Whenever we call pylast, we catch the following non-pylast exceptions, as
    # they are not caught and wrapped by pylast.
    #
    # socket.error:
    #   Not reported upstream.
    # UnicodeDecodeError:
    #   http://code.google.com/p/pylast/issues/detail?id=55
    # xml.parsers.expat.ExpatError:
    #   http://code.google.com/p/pylast/issues/detail?id=58
    # httplib.HTTPException:
    #   Not reported upstream.

    def __init__(self, core_queue, connection):
        super(LastfmFrontendThread, self).__init__(core_queue)
        self.name = u'LastfmFrontendThread'
        self.connection = connection
        self.lastfm = None
        self.last_start_time = None

    def run_inside_try(self):
        self.setup()
        while self.lastfm is not None:
            self.connection.poll(None)
            message = self.connection.recv()
            self.process_message(message)

    def setup(self):
        try:
            username = settings.LASTFM_USERNAME
            password_hash = pylast.md5(settings.LASTFM_PASSWORD)
            self.lastfm = pylast.LastFMNetwork(
                api_key=API_KEY, api_secret=API_SECRET,
                username=username, password_hash=password_hash)
            logger.info(u'Connected to Last.fm')
        except SettingsError as e:
            logger.info(u'Last.fm scrobbler not started')
            logger.debug(u'Last.fm settings error: %s', e)
        except (pylast.WSError, socket.error, UnicodeDecodeError, ExpatError,
                HTTPException) as e:
            logger.error(u'Last.fm connection error: %s', e)

    def process_message(self, message):
        if message['command'] == 'started_playing':
            self.started_playing(message['track'])
        elif message['command'] == 'stopped_playing':
            self.stopped_playing(message['track'], message['stop_position'])
        else:
            pass # Ignore commands for other frontends

    def started_playing(self, track):
        artists = ', '.join([a.name for a in track.artists])
        duration = track.length and track.length // 1000 or 0
        self.last_start_time = int(time.time())
        logger.debug(u'Now playing track: %s - %s', artists, track.name)
        try:
            self.lastfm.update_now_playing(
                artists,
                (track.name or ''),
                album=(track.album and track.album.name or ''),
                duration=str(duration),
                track_number=str(track.track_no),
                mbid=(track.musicbrainz_id or ''))
        except (pylast.ScrobblingError, pylast.WSError, socket.error,
                UnicodeDecodeError, ExpatError, HTTPException) as e:
            logger.warning(u'Last.fm now playing error: %s', e)

    def stopped_playing(self, track, stop_position):
        artists = ', '.join([a.name for a in track.artists])
        duration = track.length and track.length // 1000 or 0
        stop_position = stop_position // 1000
        if duration < 30:
            logger.debug(u'Track too short to scrobble. (30s)')
            return
        if stop_position < duration // 2 and stop_position < 240:
            logger.debug(
                u'Track not played long enough to scrobble. (50% or 240s)')
            return
        if self.last_start_time is None:
            self.last_start_time = int(time.time()) - duration
        logger.debug(u'Scrobbling track: %s - %s', artists, track.name)
        try:
            self.lastfm.scrobble(
                artists,
                (track.name or ''),
                str(self.last_start_time),
                album=(track.album and track.album.name or ''),
                track_number=str(track.track_no),
                duration=str(duration),
                mbid=(track.musicbrainz_id or ''))
        except (pylast.ScrobblingError, pylast.WSError, socket.error,
                UnicodeDecodeError, ExpatError, HTTPException) as e:
            logger.warning(u'Last.fm scrobbling error: %s', e)

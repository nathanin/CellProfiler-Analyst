from __future__ import with_statement
from dbconnect import DBConnect
from properties import Properties
from singleton import Singleton
from heapq import heappush, heappop
from weakref import WeakValueDictionary
import imagetools
import logging
import numpy
import threading
import wx

db = DBConnect.getInstance()
p = Properties.getInstance()

def load_lock():
    return TileCollection.getInstance().load_lock

class List(list):
    pass

class TileCollection(Singleton):
    '''
    Main access point for loading tiles through the TileLoader.
    '''
    def __init__(self):
        self.tileData  = WeakValueDictionary()
        self.loadq     = []
        self.cv        = threading.Condition()
        self.load_lock = threading.Lock()
        self.group_priority = 0
        # Gray placeholder for unloaded images
        self.imagePlaceholder = List([numpy.zeros((int(p.image_tile_size),
                                                   int(p.image_tile_size)))+0.1
                                      for i in range(sum(map(int,p.channels_per_image)))])
        self.loader = TileLoader(self, None)

    def GetTileData(self, obKey, notify_window, priority=1):
        return self.GetTiles([obKey], notify_window, priority)[0]
    
    def GetTiles(self, obKeys, notify_window, priority=1):
        '''
        obKeys: object tiles to fetch
        notify_window: window that will handle TileUpdatedEvent(s)
        priority: priority with which to fetch these tiles (tiles with
            smaller priorities are pushed to the front of the load queue)
            a 3-tuple is used to provide 3 tiers of priority.
        Returns: a list of lists of tile data (in numpy arrays) in the order
            of the obKeys that were passed in.
        '''
        self.loader.notify_window = notify_window
        self.group_priority -= 1
        tiles = []
        temp = {} # for weakrefs
        with self.cv:
            for order, obKey in enumerate(obKeys):
                if not obKey in self.tileData:
                    heappush(self.loadq, ((priority, self.group_priority, order), obKey))
                    self.group_priority += 1
                    temp[order] = List(self.imagePlaceholder)
                    self.tileData[obKey] = temp[order]
            tiles = [self.tileData[obKey] for obKey in obKeys]
            self.cv.notify()
        return tiles    

    
# Event generated by the TileLoader thread.
EVT_TILE_UPDATED_ID = wx.NewId()

def EVT_TILE_UPDATED(win, func):
    '''
    Any class that wishes to handle TileUpdatedEvents must call this function
    with itself as the first parameter, and a handler as the second parameter.
    '''
    win.Connect(-1, -1, EVT_TILE_UPDATED_ID, func)

   
class TileUpdatedEvent(wx.PyEvent):
    '''
    This event type is posted whenever an ImageTile has been updated by the
    TileLoader thread.
    '''
    def __init__(self, data):
        wx.PyEvent.__init__(self)
        self.SetEventType(EVT_TILE_UPDATED_ID)
        self.data = data    


class TileLoader(threading.Thread):
    '''
    This thread is owned by the TileCollection singleton and is kept
    running for the duration of the app execution.  Whenever
    TileCollection has obKeys in its load queue (loadq), this thread
    will remove them from the queue and fetch the tile data for
    them. The tile data is then written back into TileCollection's
    tileData dict over the existing placeholder. Finally an event is
    posted to the svn to tell it to refresh the tiles.
    '''
    def __init__(self, tc, notify_window):
        threading.Thread.__init__(self)
        self.setName('TileLoader_%s'%(self.getName()))
        self.notify_window = notify_window
        self.tile_collection = tc
        self._want_abort = False
        self.start()
    
    def run(self):
        try:
            from bioformats import jutil
            jutil.start_vm([])
            jutil.attach()
        except:
            import traceback
            logging.error('Error occurred while starting VM.')
            traceback.print_exc()
        while 1:            
            self.tile_collection.cv.acquire()
            # If there are no objects in the queue then wait
            while not self.tile_collection.loadq:
                self.tile_collection.cv.wait()
                
            if self._want_abort:
                self.tile_collection.cv.release()
                logging.info('%s aborted'%self.getName())
                return

            obKey = heappop(self.tile_collection.loadq)[1]
            self.tile_collection.cv.release()

            # wait until loading has completed before continuing
            with self.tile_collection.load_lock:
                # Make sure tile hasn't been deleted outside this thread
                if not self.tile_collection.tileData.get(obKey, None):
                    continue

                # Get the tile
                new_data = imagetools.FetchTile(obKey)
                if new_data is None:
                    #if fetching fails, leave the tile blank
                    continue
                
                tile_data = self.tile_collection.tileData.get(obKey, None)
                
                # Make sure tile hasn't been deleted outside this thread
                if tile_data is not None:
                    # copy each channel
                    for i in range(len(tile_data)):
                        tile_data[i] = new_data[i]
                    wx.PostEvent(self.notify_window, TileUpdatedEvent(obKey))

    def abort(self):
        self._want_abort = True
        self.tile_collection.cv.acquire()
        heappush(self.tile_collection.loadq, ((0, 0, 0), '<ABORT>'))
        self.tile_collection.cv.notify()
        self.tile_collection.cv.release()        



################# FOR TESTING ##########################
if __name__ == "__main__":
    app = wx.PySimpleApp()

    
    from datamodel import DataModel
    p = Properties.getInstance()
    p.LoadFile('../properties/nirht_test.properties')
    db = DBConnect.getInstance()
    db.connect()
    dm = DataModel.getInstance()
    
    test = TileCollection.getInstance()
    
    f =  wx.Frame(None)
    for i in xrange(10):
        obKey = dm.GetRandomObject()
        test.GetTileData((0,1,1), f)
        
    for t in threading.enumerate():
        if t != threading.currentThread():
            t.abort()
    f.Destroy()
    
    app.MainLoop()

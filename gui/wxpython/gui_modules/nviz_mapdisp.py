"""!
@package nviz_mapdisp.py

@brief wxGUI 3D view mode (map canvas)

This module implements 3D visualization mode for map display.

List of classes:
 - NvizThread
 - GLWindow

(C) 2008-2011 by the GRASS Development Team

This program is free software under the GNU General Public
License (>=v2). Read the file COPYING that comes with GRASS
for details.

@author Martin Landa <landa.martin gmail.com> (Google SoC 2008/2010)
@author Anna Kratochvilova <KratochAnna seznam.cz> (Google SoC 2011)
"""

import os
import sys
import time
import copy
import math

from threading import Thread

import wx
import wx.lib.scrolledpanel as scrolled
from wx.lib.newevent import NewEvent
from wx import glcanvas

import gcmd
import globalvar
from debug          import Debug
from mapdisp_window import MapWindow
from goutput        import wxCmdOutput
from preferences    import globalSettings as UserSettings
from workspace      import Nviz as NvizDefault

import wxnviz

wxUpdateProperties, EVT_UPDATE_PROP  = NewEvent()
wxUpdateView,       EVT_UPDATE_VIEW  = NewEvent()
wxUpdateLight,      EVT_UPDATE_LIGHT = NewEvent()
wxUpdateCPlane,     EVT_UPDATE_CPLANE = NewEvent()

class NvizThread(Thread):
    def __init__(self, log, progressbar, window):
        Thread.__init__(self)
        Debug.msg(5, "NvizThread.__init__():")
        self.log = log
        self.progressbar = progressbar
        self.window = window
        
        self._display = None
        
        self.setDaemon(True)

    def run(self):
        self._display = wxnviz.Nviz(self.log, self.progressbar)
        
    def GetDisplay(self):
        """!Get display instance"""
        return self._display
    
class GLWindow(MapWindow, glcanvas.GLCanvas):
    """!OpenGL canvas for Map Display Window"""
    def __init__(self, parent, id = wx.ID_ANY,
                 Map = None, tree = None, lmgr = None):
        self.parent = parent # MapFrame
        Debug.msg(5, "GLCanvas.__init__(): begin")
        glcanvas.GLCanvas.__init__(self, parent, id)
        MapWindow.__init__(self, parent, id, 
                           Map, tree, lmgr)
        self.Hide()
        
        self.init = False
        self.initView = False
        
        # render mode 
        self.render = { 'quick' : False,
                        # do not render vector lines in quick mode
                        'vlines' : False,
                        'vpoints' : False }
        self.mouse = {
            'use': 'default'
            }
        self.cursors = {
            'default' : wx.StockCursor(wx.CURSOR_ARROW),
            'cross'   : wx.StockCursor(wx.CURSOR_CROSS),
            }
        # list of loaded map layers (layer tree items)
        self.layers  = list()
        # list of constant surfaces
        self.constants = list()
        # list of cutting planes
        self.cplanes = list()
        # list of query points
        self.qpoints = list()
        
        #
        # use display region instead of computational
        #
        os.environ['GRASS_REGION'] = self.Map.SetRegion()
        
        #
        # create nviz instance
        #
        if self.lmgr:
            self.log = self.lmgr.goutput
            logerr = self.lmgr.goutput.cmd_stderr
            logmsg = self.lmgr.goutput.cmd_output
        else:
            self.log = logmsg = sys.stdout
            logerr = sys.stderr
        
        self.nvizThread = NvizThread(logerr,
                                     self.parent.statusbarWin['progress'],
                                     logmsg)
        self.nvizThread.start()
        time.sleep(.1)
        self._display = self.nvizThread.GetDisplay()
        
        # GRASS_REGION needed only for initialization
        del os.environ['GRASS_REGION']
        
        self.img = wx.Image(self.Map.mapfile, wx.BITMAP_TYPE_ANY)
        
        # size of MapWindow, to avoid resizing if size is the same
        self.size = (0,0)
        
        #
        # default values
        #
        self.view = copy.deepcopy(UserSettings.Get(group = 'nviz', key = 'view')) # copy
        self.iview = UserSettings.Get(group = 'nviz', key = 'view', internal = True)
        
        self.nvizDefault = NvizDefault()
        self.light = copy.deepcopy(UserSettings.Get(group = 'nviz', key = 'light')) # copy
        
        self.Bind(wx.EVT_ERASE_BACKGROUND, self.OnEraseBackground)
        self.Bind(wx.EVT_SIZE,             self.OnSize)
        self.Bind(wx.EVT_PAINT,            self.OnPaint)
        self.Bind(wx.EVT_LEFT_UP,          self.OnLeftUp)
        self.Bind(wx.EVT_MOUSE_EVENTS,     self.OnMouseAction)
        self.Bind(wx.EVT_MOTION,           self.OnMotion)
        
        self.Bind(EVT_UPDATE_PROP,  self.UpdateMapObjProperties)
        self.Bind(EVT_UPDATE_VIEW,  self.UpdateView)
        self.Bind(EVT_UPDATE_LIGHT, self.UpdateLight)
        self.Bind(EVT_UPDATE_CPLANE, self.UpdateCPlane)
        
        self.Bind(wx.EVT_CLOSE, self.OnClose)
        
        Debug.msg(5, "GLCanvas.__init__(): end")
        #cplanes cannot be initialized now
        wx.CallAfter(self.InitCPlanes)
        
    def InitCPlanes(self):
        """!Initialize cutting planes list"""
        for i in range(self._display.GetCPlanesCount()):
            cplane = copy.deepcopy(UserSettings.Get(group = 'nviz', key = 'cplane'))
            self.cplanes.append(cplane)
            
            
    def OnClose(self, event):
        # cleanup when window actually closes (on quit) and not just is hidden
        self.Reset()
        
    def OnEraseBackground(self, event):
        pass # do nothing, to avoid flashing on MSW
    
    def OnSize(self, event):
        size = self.GetClientSize()
        if self.size != size \
            and self.GetContext():
            Debug.msg(3, "GLCanvas.OnSize(): w = %d, h = %d" % \
                      (size.width, size.height))
            self.SetCurrent()
            self._display.ResizeWindow(size.width,
                                       size.height)
        self.size = size
        event.Skip()
        
    def OnPaint(self, event):
        Debug.msg(1, "GLCanvas.OnPaint()")
        
        dc = wx.PaintDC(self)
        self.DoPaint()

    def DoPaint(self):
        self.SetCurrent()
        
        if not self.initView:
            self._display.InitView()
            self.initView = True
        
        self.LoadDataLayers()
        self.UnloadDataLayers()
        
        if not self.init:
            self.ResetView()
            
            if hasattr(self.lmgr, "nviz"):
                self.lmgr.nviz.UpdatePage('view')
                self.lmgr.nviz.UpdatePage('light')
                self.lmgr.nviz.UpdatePage('cplane')
                layer = self.GetSelectedLayer()
                if layer:
                    if layer.type ==  'raster':
                        self.lmgr.nviz.UpdatePage('surface')
                        self.lmgr.nviz.UpdatePage('fringe')
                    elif layer.type ==  'vector':
                        self.lmgr.nviz.UpdatePage('vector')
                
                self.lmgr.nviz.UpdateSettings()
                
                # update widgets
                win = self.lmgr.nviz.FindWindowById( \
                    self.lmgr.nviz.win['vector']['lines']['surface'])
                win.SetItems(self.GetLayerNames('raster'))
            
            self.init = True
        
        self.UpdateMap()
                
    def OnMouseAction(self, event):
        # change perspective with mouse wheel
        wheel = event.GetWheelRotation()
        
        if wheel !=  0:
            current  = event.GetPositionTuple()[:]
            Debug.msg (5, "GLWindow.OnMouseMotion(): wheel = %d" % wheel)
            prev_value = self.view['persp']['value']
            if wheel > 0:
                value = -1 * self.view['persp']['step']
            else:
                value = self.view['persp']['step']
            self.view['persp']['value'] +=  value
            if self.view['persp']['value'] < 1:
                self.view['persp']['value'] = 1
            elif self.view['persp']['value'] > 100:
                self.view['persp']['value'] = 100
            
            if prev_value !=  self.view['persp']['value']:
                if hasattr(self.lmgr, "nviz"):
                    self.lmgr.nviz.UpdateSettings()
                    
                    self._display.SetView(self.view['position']['x'], self.view['position']['y'],
                                          self.iview['height']['value'],
                                          self.view['persp']['value'],
                                          self.view['twist']['value'])
                
                # redraw map
                self.DoPaint()
                
                # update statusbar
                ### self.parent.StatusbarUpdate()
        
        if event.LeftDown():
            if self.mouse['use'] == "lookHere":
                pos = event.GetPosition()
                size = self.GetClientSize()
                self._display.LookHere(pos[0], size[1] - pos[1])
                self.Refresh(False)
                focus = self._display.GetFocus()
                for i, coord in enumerate(('x', 'y', 'z')):
                    self.iview['focus'][coord] = focus[i]
                toggle = self.lmgr.nviz.FindWindowByName('here')
                toggle.SetValue(False)
                self.mouse['use'] = 'default'
                self.SetCursor(self.cursors['default'])
                
        event.Skip()

    def Pixel2Cell(self, (x, y)):
        """!Convert image coordinates to real word coordinates

        @param x, y image coordinates
        
        @return easting, northing
        @return None on error
        """
        size = self.GetClientSize()
        # UL -> LL
        sid, x, y, z = self._display.GetPointOnSurface(x, y)
        
        if not sid:
            return None
        
        return (x, y)
    
    def OnLeftUp(self, event):
        self.ReleaseMouse()
        if self.mouse["use"] == "nvizQuerySurface":
            self.OnQuerySurface(event)
        elif self.mouse["use"] == "nvizQueryVector":
            self.OnQueryVector(event)
    
    def OnQuerySurface(self, event):
        """!Query surface on given position"""
        result = self._display.QueryMap(event.GetX(), event.GetY())
        if result:
            self.qpoints.append((result['x'], result['y'], result['z']))
            self.log.WriteLog("%-30s: %.3f" % (_("Easting"),   result['x']))
            self.log.WriteLog("%-30s: %.3f" % (_("Northing"),  result['y']))
            self.log.WriteLog("%-30s: %.3f" % (_("Elevation"), result['z']))
            self.log.WriteLog("%-30s: %s" % (_("Surface map elevation"), result['elevation']))
            self.log.WriteLog("%-30s: %s" % (_("Surface map color"), result['color']))
            if len(self.qpoints) > 1:
                prev = self.qpoints[-2]
                curr = self.qpoints[-1]
                dxy = math.sqrt(pow(prev[0]-curr[0], 2) +
                                pow(prev[1]-curr[1], 2))
                dxyz = math.sqrt(pow(prev[0]-curr[0], 2) +
                                 pow(prev[1]-curr[1], 2) +
                                 pow(prev[2]-curr[2], 2))
                self.log.WriteLog("%-30s: %.3f" % (_("XY distance from previous"), dxy))
                self.log.WriteLog("%-30s: %.3f" % (_("XYZ distance from previous"), dxyz))
                self.log.WriteLog("%-30s: %.3f" % (_("Distance along surface"),
                                              self._display.GetDistanceAlongSurface(result['id'],
                                                                                    (curr[0], curr[1]),
                                                                                    (prev[0], prev[1]),
                                                                                    useExag = False)))
                self.log.WriteLog("%-30s: %.3f" % (_("Distance along exag. surface"),
                                              self._display.GetDistanceAlongSurface(result['id'],
                                                                                    (curr[0], curr[1]),
                                                                                    (prev[0], prev[1]),
                                                                                      useExag = True)))
            self.log.WriteCmdLog('-' * 80)
        else:
            self.log.WriteLog(_("No point on surface"))
            self.log.WriteCmdLog('-' * 80)
    
    def OnQueryVector(self, event):
        """!Query vector on given position"""
        self.log.WriteWarning(_("Function not implemented yet"))
        self.log.WriteCmdLog('-' * 80)
        
    def UpdateView(self, event):
        """!Change view settings"""
        data = self.view
        self._display.SetView(data['position']['x'], data['position']['y'],
                              self.iview['height']['value'],
                              data['persp']['value'],
                              data['twist']['value'])
        
        if event and event.zExag and 'value' in data['z-exag']:
            self._display.SetZExag(data['z-exag']['value'])
        if self.iview['focus']['x'] != -1:
            self._display.SetFocus(self.iview['focus']['x'], self.iview['focus']['y'],
                                   self.iview['focus']['z'])
        
        if event:
            event.Skip()

    def UpdateLight(self, event):
        """!Change light settings"""
        data = self.light
        self._display.SetLight(x = data['position']['x'], y = data['position']['y'],
                               z = data['position']['z'] / 100., color = data['color'],
                               bright = data['bright'] / 100.,
                               ambient = data['ambient'] / 100.)
        self._display.DrawLightingModel()
        if hasattr(event, 'refresh'):
            self.Refresh(False)
        
    def UpdateMap(self, render = True):
        """!Updates the canvas anytime there is a change to the
        underlaying images or to the geometry of the canvas.
        
        @param render re-render map composition
        """
        start = time.clock()
        
        self.resize = False
        
        if self.render['quick'] is False:
            self.parent.statusbarWin['progress'].Show()
            self.parent.statusbarWin['progress'].SetRange(2)
            self.parent.statusbarWin['progress'].SetValue(0)
        
        if self.render['quick'] is False:
            self.parent.statusbarWin['progress'].SetValue(1)
            self._display.Draw(False, -1)
        elif self.render['quick'] is True:
            # quick
            mode = wxnviz.DRAW_QUICK_SURFACE | wxnviz.DRAW_QUICK_VOLUME
            if self.render['vlines']:
                mode |=  wxnviz.DRAW_QUICK_VLINES
            if self.render['vpoints']:
                mode |=  wxnviz.DRAW_QUICK_VPOINTS
            self._display.Draw(True, mode)
        else: # None -> reuse last rendered image
            pass # TODO
            
        self.SwapBuffers()
        # draw fringe after SwapBuffers, otherwise it don't have to be visible
        # on some computers
        if self.render['quick'] is False:
            self._display.DrawFringe()
        
        stop = time.clock()
        
        if self.render['quick'] is False:
            self.parent.statusbarWin['progress'].SetValue(2)
            # hide process bar
            self.parent.statusbarWin['progress'].Hide()
        
        Debug.msg(3, "GLWindow.UpdateMap(): quick = %d, -> time = %g" % \
                      (self.render['quick'], (stop-start)))
        
    def EraseMap(self):
        """!Erase the canvas
        """
        self._display.EraseMap()
        self.SwapBuffers()
        
    def IsLoaded(self, item):
        """!Check if layer (item) is already loaded
        
        @param item layer item
        """
        layer = self.tree.GetPyData(item)[0]['maplayer']
        data = self.tree.GetPyData(item)[0]['nviz']
        
        if not data:
            return 0
        
        if layer.type ==  'raster':
            if 'object' not in data['surface']:
                return 0
        elif layer.type ==  'vector':
            if 'object' not in data['vlines'] and \
                    'object' not in data['points']:
                return 0
        
        return 1

    def _GetDataLayers(self, item, litems):
        """!Return get list of enabled map layers"""
        # load raster & vector maps
        while item and item.IsOk():
            type = self.tree.GetPyData(item)[0]['type']
            if type ==  'group':
                subItem = self.tree.GetFirstChild(item)[0]
                self._GetDataLayers(subItem, litems)
                item = self.tree.GetNextSibling(item)
                
            if not item.IsChecked() or \
                    type not in ('raster', 'vector', '3d-raster'):
                item = self.tree.GetNextSibling(item)
                continue
            
            litems.append(item)
            
            item = self.tree.GetNextSibling(item)
        
    def LoadDataLayers(self):
        """!Load raster/vector from current layer tree
        
        @todo volumes
        """
        if not self.tree:
            return
        
        listOfItems = []
        item = self.tree.GetFirstChild(self.tree.root)[0]
        self._GetDataLayers(item, listOfItems)
        
        start = time.time()
        
        while(len(listOfItems) > 0):
            item = listOfItems.pop()
            type = self.tree.GetPyData(item)[0]['type']
            if item in self.layers:
                continue
            # "raster (double click to set properties)" - tries to load this 
            # layer - no idea how to fix it
            if ' ' in self.tree.GetPyData(item)[0]['maplayer'].name:
                return
            try:
                if type ==  'raster':
                    self.LoadRaster(item)
                elif type ==  '3d-raster':
                    self.LoadRaster3d(item)
                elif type ==  'vector':
                    # data = self.tree.GetPyData(item)[0]['nviz']
                    # vecType = []
                    # if data and 'vector' in data:
                    #     for v in ('lines', 'points'):
                    #         if data['vector'][v]:
                    #             vecType.append(v)
                    layer = self.tree.GetPyData(item)[0]['maplayer']
                    npoints, nlines, nfeatures, mapIs3D = self.lmgr.nviz.VectorInfo(layer)
                    if npoints > 0:
                        self.LoadVector(item, points = True)
                    if nlines > 0:
                        self.LoadVector(item, points = False)
            except gcmd.GException, e:
                GError(parent = self,
                       message = e.value)
            self.init = False
        
        stop = time.time()
        
        Debug.msg(3, "GLWindow.LoadDataLayers(): time = %f" % (stop-start))
                
    def UnloadDataLayers(self):
        """!Unload any layers that have been deleted from layer tree"""
        if not self.tree:
            return
        
        listOfItems = []
        item = self.tree.GetFirstChild(self.tree.root)[0]
        self._GetDataLayers(item, listOfItems)
        
        start = time.time()
        
        for layer in self.layers:
            if layer not in listOfItems:
                ltype = self.tree.GetPyData(layer)[0]['type']
                try:
                    if ltype ==  'raster':
                        self.UnloadRaster(layer)
                    elif ltype ==  '3d-raster':
                        self.UnloadRaster3d(layer) 
                    elif ltype ==  'vector':
                        self.UnloadVector(layer, True)
                        self.UnloadVector(layer, False)
                    
                    self.UpdateView(None)
                except gcmd.GException, e:
                    gcmd.GError(parent = self,
                                message = e.value)
                
                self.lmgr.nviz.UpdateSettings()        
        
        stop = time.time()
        
        Debug.msg(3, "GLWindow.UnloadDataLayers(): time = %f" % (stop-start))        
        
    def SetVectorSurface(self, data):
        """!Set reference surfaces of vector"""
        data['mode']['surface'] = {}
        data['mode']['surface']['value'] = list()
        data['mode']['surface']['show'] = list()
        for name in self.GetLayerNames('raster'):
            data['mode']['surface']['value'].append(name)
            data['mode']['surface']['show'].append(True)
        
    def SetVectorFromCmd(self, item, data):
        """!Set 3D view properties from cmd (d.vect)

        @param item Layer Tree item
        @param nviz data
        """
        cmd = self.tree.GetPyData(item)[0]['cmd']
        if cmd[0] != 'd.vect':
            return
        for opt in cmd[1:]:
            try:
                key, value = opt.split('=')
            except ValueError:
                continue
            if key == 'color':
                data['lines']['color']['value'] = value
                data['points']['color']['value'] = value

    def SetMapObjProperties(self, item, id, nvizType):
        """!Set map object properties
        
        Properties must be afterwards updated by
        UpdateMapObjProperties().
        
        @param item layer item
        @param id nviz layer id (or -1)
        @param nvizType nviz data type (surface, points, vector)
        """
        if nvizType != 'constant':
            type = self.tree.GetPyData(item)[0]['maplayer'].type
            # reference to original layer properties (can be None)
            data = self.tree.GetPyData(item)[0]['nviz']
        else:
            type = nvizType
            data = self.constants[item]
            
        if not data:
            # init data structure
            if nvizType != 'constant':
                self.tree.GetPyData(item)[0]['nviz'] = {}
                data = self.tree.GetPyData(item)[0]['nviz']
            
            if type ==  'raster':
                # reset to default properties
                data[nvizType] = self.nvizDefault.SetSurfaceDefaultProp()
                        
            elif type ==  'vector':
                # reset to default properties (lines/points)
                data['vector'] = self.nvizDefault.SetVectorDefaultProp()
                self.SetVectorFromCmd(item, data['vector'])
                self.SetVectorSurface(data['vector']['points'])
                self.SetVectorSurface(data['vector']['lines'])
                
            elif type ==  '3d-raster':
                # reset to default properties 
                data[nvizType] = self.nvizDefault.SetVolumeDefaultProp()
                
            elif type == 'constant':
                data['constant'] = self.nvizDefault.SetConstantDefaultProp()
        
        else:
            # complete data (use default values), not sure if this is necessary
            if type ==  'raster':
                if not data['surface']:
                    data['surface'] = self.nvizDefault.SetSurfaceDefaultProp()
            if type ==  'vector':
                if not data['vector']['lines']:
                    self.nvizDefault.SetVectorLinesDefaultProp(data['vector']['lines'])
                if not data['vector']['points']:
                    self.nvizDefault.SetVectorPointsDefaultProp(data['vector']['points'])
                    
            # set updates
            for sec in data.keys():
                for sec1 in data[sec].keys():
                    if sec1 == 'position':
                        data[sec][sec1]['update'] = None
                        continue
                    for sec2 in data[sec][sec1].keys():
                        if sec2 !=  'all':
                            data[sec][sec1][sec2]['update'] = None
            event = wxUpdateProperties(data = data)
            wx.PostEvent(self, event)
        
        # set id
        if id > 0:
            if type in ('raster', '3d-raster'):
               data[nvizType]['object'] = { 'id' : id,
                                            'init' : False }
            elif type ==  'vector':
                data['vector'][nvizType]['object'] = { 'id' : id,
                                                       'init' : False }
            elif type ==  'constant':
                data[nvizType]['object'] = { 'id' : id,
                                             'init' : False }
        
        return data

    def LoadRaster(self, item):
        """!Load 2d raster map and set surface attributes
        
        @param layer item
        """
        return self._loadRaster(item)
    
    def LoadRaster3d(self, item):
        """!Load 3d raster map and set surface attributes
        
        @param layer item
        """
        return self._loadRaster(item)
    
    def _loadRaster(self, item):
        """!Load 2d/3d raster map and set its attributes
        
        @param layer item
        """
        layer = self.tree.GetPyData(item)[0]['maplayer']
        
        if layer.type not in ('raster', '3d-raster'):
            return
        
        if layer.type ==  'raster':
            id = self._display.LoadSurface(str(layer.name), None, None)
            nvizType = 'surface'
            errorMsg = _("Loading raster map")
        elif layer.type ==  '3d-raster':
            id = self._display.LoadVolume(str(layer.name), None, None)
            nvizType = 'volume'
            errorMsg = _("Loading 3d raster map")
        else:
            id = -1
        
        if id < 0:
            if layer.type in ('raster', '3d-raster'):
                self.log.WriteError("%s <%s> %s" % (errorMsg, layer.name, _("failed")))
            else:
                self.log.WriteError(_("Unsupported layer type '%s'") % layer.type)
        
        self.layers.append(item)
        
        # set default/workspace layer properties
        data = self.SetMapObjProperties(item, id, nvizType)
        
        # update properties
        event = wxUpdateProperties(data = data)
        wx.PostEvent(self, event)
        
        # update tools window
        if hasattr(self.lmgr, "nviz") and \
                item ==  self.GetSelectedLayer(type = 'item'):
            toolWin = self.lmgr.nviz
            if layer.type ==  'raster':
                win = toolWin.FindWindowById( \
                    toolWin.win['vector']['lines']['surface'])
                win.SetItems(self.GetLayerNames(layer.type))
            
            #toolWin.UpdatePage(nvizType)
            #toolWin.SetPage(nvizType)
        
        return id
    
    def NewConstant(self):
        """!Create new constant"""
        index = len(self.constants)
        try:
            name = self.constants[-1]['constant']['object']['name'] + 1
        except IndexError:
            name = 1
        data = dict()
        self.constants.append(data)
        data = self.SetMapObjProperties(item = index, id = -1, nvizType = 'constant')
        self.AddConstant(data, name)
        return name
        
    def AddConstant(self, data, name):
        """!Add new constant"""
        id = self._display.AddConstant(value = data['constant']['value'], color = data['constant']['color'])
        self._display.SetSurfaceRes(id, data['constant']['resolution'], data['constant']['resolution'])
        data['constant']['object'] = { 'id' : id,
                                       'name': name,
                                       'init' : False }
    
    def DeleteConstant(self, index):
        """!Delete constant layer"""
        id = self.constants[index]['constant']['object']['id']
        self._display.UnloadSurface(id)
        del self.constants[index]
    
    def SelectCPlane(self, index):
        """!Select cutting plane"""
        for plane in range (self._display.GetCPlanesCount()):
            if plane == index:
                self._display.SelectCPlane(plane)
            else:
                self._display.UnselectCPlane(plane)
    
    def UpdateCPlane(self, event):
        """!Change cutting plane settings"""
        current = event.current
        for each in event.update:
            if each == 'rotation':
                self._display.SetCPlaneRotation(0, self.cplanes[current]['rotation']['tilt'],
                                                   self.cplanes[current]['rotation']['rot'])
            if each == 'position':
                self._display.SetCPlaneTranslation(self.cplanes[current]['position']['x'],
                                                   self.cplanes[current]['position']['y'],
                                                   self.cplanes[current]['position']['z'])
            if each == 'shading':
                self._display.SetFenceColor(self.cplanes[current]['shading'])
            
    def UnloadRaster(self, item):
        """!Unload 2d raster map
        
        @param layer item
        """
        return self._unloadRaster(item)
    
    def UnloadRaster3d(self, item):
        """!Unload 3d raster map
        
        @param layer item
        """
        return self._unloadRaster(item)
    
    def _unloadRaster(self, item):
        """!Unload 2d/3d raster map
        
        @param item layer item
        """
        layer = self.tree.GetPyData(item)[0]['maplayer']
        
        if layer.type not in ('raster', '3d-raster'):
            return
        
        data = self.tree.GetPyData(item)[0]['nviz']
        
        if layer.type ==  'raster':
            nvizType = 'surface'
            unloadFn = self._display.UnloadSurface
            errorMsg = _("Unable to unload raster map")
            successMsg = _("Raster map")
        else:
            nvizType = 'volume'
            unloadFn = self._display.UnloadVolume
            errorMsg = _("Unable to unload 3d raster map")
            successMsg = _("3d raster map")
        
        id = data[nvizType]['object']['id']
        
        if unloadFn(id) ==  0:
            self.log.WriteError("%s <%s>" % (errorMsg, layer.name))
        else:
            self.log.WriteLog("%s <%s> %s" % (successMsg, layer.name, _("unloaded successfully")))
        
        data[nvizType].pop('object')
        
        self.layers.remove(item)
        
        # update tools window
        if hasattr(self.lmgr, "nviz") and \
                layer.type ==  'raster':
            toolWin = self.lmgr.nviz
            win = toolWin.FindWindowById( \
                toolWin.win['vector']['lines']['surface'])
            win.SetItems(self.GetLayerNames(layer.type))
            
    def LoadVector(self, item, points = None):
        """!Load 2D or 3D vector map overlay
        
        @param item layer item
        @param points True to load points, False to load lines, None
        to load both
        """
        layer = self.tree.GetPyData(item)[0]['maplayer']
        if layer.type !=  'vector':
            return
        
        # set default properties
        if points is None:
            self.SetMapObjProperties(item, -1, 'lines')
            self.SetMapObjProperties(item, -1, 'points')
            vecTypes = ('points', 'lines')
        elif points:
            self.SetMapObjProperties(item, -1, 'points')
            vecTypes = ('points', )
        else:
            self.SetMapObjProperties(item, -1, 'lines')
            vecTypes = ('lines', )
        
        id = -1
        for vecType in vecTypes:
            if vecType == 'lines':
                id = self._display.LoadVector(str(layer.GetName()), False)
            else:
                id = self._display.LoadVector(str(layer.GetName()), True)
            if id < 0:
                self.log.WriteError(_("Loading vector map <%(name)s> (%(type)s) failed") % \
                    { 'name' : layer.name, 'type' : vecType })
            # update layer properties
            self.SetMapObjProperties(item, id, vecType)
        
        self.layers.append(item)
        
        # update properties
        data = self.tree.GetPyData(item)[0]['nviz']
        event = wxUpdateProperties(data = data)
        wx.PostEvent(self, event)
        
        # update tools window
        if hasattr(self.lmgr, "nviz") and \
                item ==  self.GetSelectedLayer(type = 'item'):
            toolWin = self.lmgr.nviz
            
            toolWin.UpdatePage('vector')
            ### toolWin.SetPage('vector')
        
        return id

    def UnloadVector(self, item, points = None):
        """!Unload vector map overlay
        
        @param item layer item
        @param points,lines True to unload given feature type
        """
        layer = self.tree.GetPyData(item)[0]['maplayer']
        data = self.tree.GetPyData(item)[0]['nviz']['vector']
        
        # if vecType is None:
        #     vecType = []
        #     for v in ('lines', 'points'):
        #         if UserSettings.Get(group = 'nviz', key = 'vector',
        #                             subkey = [v, 'show']):
        #             vecType.append(v)
        
        if points is None:
            vecTypes = ('points', 'lines')
        elif points:
            vecTypes = ('points', )
        else:
            vecTypes = ('lines', )
        
        for vecType in vecTypes:
            if 'object' not in data[vecType]:
                continue
            
            id = data[vecType]['object']['id']
            
            if vecType ==  'lines':
                ret = self._display.UnloadVector(id, False)
            else:
                ret = self._display.UnloadVector(id, True)
            if ret ==  0:
                self.log.WriteError(_("Unable to unload vector map <%(name)s> (%(type)s)") % \
                    { 'name': layer.name, 'type' : vecType })
            else:
                self.log.WriteLog(_("Vector map <%(name)s> (%(type)s) unloaded successfully") % \
                    { 'name' : layer.name, 'type' : vecType })
            
            data[vecType].pop('object')
            
            ### self.layers.remove(id)
        
    def Reset(self):
        """!Reset (unload data)"""
        for item in self.layers:
            type = self.tree.GetPyData(item)[0]['maplayer'].type
            if type ==  'raster':
                self.UnloadRaster(item)
            elif type ==  '3d-raster':
                self.UnloadRaster3d(item)
            elif type ==  'vector':
                self.UnloadVector(item)
        
        self.init = False

    def OnZoomToMap(self, event):
        """!Set display extents to match selected raster or vector
        map or volume.
        
        @todo vector, volume
        """
        layer = self.GetSelectedLayer()
        
        if layer is None:
            return
        
        Debug.msg (3, "GLWindow.OnZoomToMap(): layer = %s, type = %s" % \
                       (layer.name, layer.type))
        
        self._display.SetViewportDefault()

    def ResetView(self):
        """!Reset to default view"""
        self.view['z-exag']['value'], \
            self.iview['height']['value'], \
            self.iview['height']['min'], \
            self.iview['height']['max'] = self._display.SetViewDefault()
        
        self.view['z-exag']['min'] = 0
        self.view['z-exag']['max'] = self.view['z-exag']['value'] * 10
        
        self.view['position']['x'] = UserSettings.Get(group = 'nviz', key = 'view',
                                                 subkey = ('position', 'x'))
        self.view['position']['y'] = UserSettings.Get(group = 'nviz', key = 'view',
                                                 subkey = ('position', 'y'))
        self.view['persp']['value'] = UserSettings.Get(group = 'nviz', key = 'view',
                                                       subkey = ('persp', 'value'))
        
        self.view['twist']['value'] = UserSettings.Get(group = 'nviz', key = 'view',
                                                       subkey = ('twist', 'value'))
                                                    
        self._display.LookAtCenter()
        focus = self.iview['focus']
        focus['x'], focus['y'], focus['z'] = self._display.GetFocus()
        
        event = wxUpdateView(zExag = False)
        wx.PostEvent(self, event)
        
    def UpdateMapObjProperties(self, event):
        """!Generic method to update data layer properties"""
        data = event.data
        
        if 'surface' in data:
            id = data['surface']['object']['id']
            self.UpdateSurfaceProperties(id, data['surface'])
            # -> initialized
            data['surface']['object']['init'] = True
            
        elif 'constant' in data:
            id = data['constant']['object']['id']
            self.UpdateConstantProperties(id, data['constant'])
            # -> initialized
            data['constant']['object']['init'] = True  
              
        elif 'volume' in data:
            id = data['volume']['object']['id']
            self.UpdateVolumeProperties(id, data['volume'])
            # -> initialized
            data['volume']['object']['init'] = True
            
        elif 'vector' in data:
            for type in ('lines', 'points'):
                if 'object' in data['vector'][type]:
                    id = data['vector'][type]['object']['id']
                    self.UpdateVectorProperties(id, data['vector'], type)
                    # -> initialized
                    data['vector'][type]['object']['init'] = True
    
    def UpdateConstantProperties(self, id, data):
        """!Update surface map object properties"""
        self._display.SetSurfaceColor(id = id, map = False, value = data['color'])
        self._display.SetSurfaceTopo(id = id, map = False, value = data['value'])
        self._display.SetSurfaceRes(id, data['resolution'], data['resolution'])
            
    def UpdateSurfaceProperties(self, id, data):
        """!Update surface map object properties"""
        # surface attributes
        for attrb in ('color', 'mask',
                     'transp', 'shine'):
            if attrb not in data['attribute'] or \
                    'update' not in data['attribute'][attrb]:
                continue
            
            map = data['attribute'][attrb]['map']
            value = data['attribute'][attrb]['value']
            
            if map is None: # unset
                # only optional attributes
                if attrb ==  'mask':
                    # TODO: invert mask
                    # TODO: broken in NVIZ
                    self._display.UnsetSurfaceMask(id)
                elif attrb ==  'transp':
                    self._display.UnsetSurfaceTransp(id) 
            else:
                if type(value) ==  type('') and \
                        len(value) <=  0: # ignore empty values (TODO: warning)
                    continue
                if attrb ==  'color':
                    self._display.SetSurfaceColor(id, map, str(value))
                elif attrb ==  'mask':
                    # TODO: invert mask
                    # TODO: broken in NVIZ
                    self._display.SetSurfaceMask(id, False, str(value))
                elif attrb ==  'transp':
                    self._display.SetSurfaceTransp(id, map, str(value)) 
                elif attrb ==  'shine':
                    self._display.SetSurfaceShine(id, map, str(value)) 
            data['attribute'][attrb].pop('update')
        
        # draw res
        if 'update' in data['draw']['resolution']:
            coarse = data['draw']['resolution']['coarse']
            fine   = data['draw']['resolution']['fine']
            
            if data['draw']['all']:
                self._display.SetSurfaceRes(-1, fine, coarse)
            else:
                self._display.SetSurfaceRes(id, fine, coarse)
            data['draw']['resolution'].pop('update')
        
        # draw style
        if 'update' in data['draw']['mode']:
            if data['draw']['mode']['value'] < 0: # need to calculate
                data['draw']['mode']['value'] = \
                    self.nvizDefault.GetDrawMode(mode = data['draw']['mode']['desc']['mode'],
                                                 style = data['draw']['mode']['desc']['style'],
                                                 shade = data['draw']['mode']['desc']['shading'],
                                                 string = True)
            style = data['draw']['mode']['value']
            if data['draw']['all']:
                self._display.SetSurfaceStyle(-1, style)
            else:
                self._display.SetSurfaceStyle(id, style)
            data['draw']['mode'].pop('update')
        
        # wire color
        if 'update' in data['draw']['wire-color']:
            color = data['draw']['wire-color']['value']
            if data['draw']['all']:
                self._display.SetWireColor(-1, str(color))
            else:
                self._display.SetWireColor(id, str(color))
            data['draw']['wire-color'].pop('update')
        
        # position
        if 'update' in data['position']:
            x = data['position']['x']
            y = data['position']['y']
            z = data['position']['z']
            self._display.SetSurfacePosition(id, x, y, z)
            data['position'].pop('update')
        data['draw']['all'] = False
        
    def UpdateVolumeProperties(self, id, data, isosurfId = None):
        """!Update volume (isosurface/slice) map object properties"""
        if 'update' in data['draw']['resolution']:
            self._display.SetIsosurfaceRes(id, data['draw']['resolution']['value'])
            data['draw']['resolution'].pop('update')
        
        if 'update' in data['draw']['shading']:
            if data['draw']['shading']['value'] < 0: # need to calculate
                data['draw']['shading']['value'] = \
                    self.nvizDefault.GetDrawMode(shade = data['draw']['shading'],
                                                 string = False)
            data['draw']['shading'].pop('update')
        
        #
        # isosurface attributes
        #
        isosurfId = 0
        for isosurf in data['isosurface']:
            for attrb in ('color', 'mask',
                          'transp', 'shine', 'emit'):
                if attrb not in isosurf or \
                        'update' not in isosurf[attrb]:
                    continue
                map = isosurf[attrb]['map']
                value = isosurf[attrb]['value']
                
                if map is None: # unset
                    # only optional attributes
                    if attrb ==  'mask':
                        # TODO: invert mask
                        # TODO: broken in NVIZ
                        self._display.UnsetIsosurfaceMask(id, isosurfId)
                    elif attrb ==  'transp':
                        self._display.UnsetIsosurfaceTransp(id, isosurfId)
                    elif attrb ==  'emit':
                        self._display.UnsetIsosurfaceEmit(id, isosurfId) 
                else:
                    if type(value) ==  type('') and \
                            len(value) <=  0: # ignore empty values (TODO: warning)
                        continue
                    elif attrb ==  'color':
                        self._display.SetIsosurfaceColor(id, isosurfId, map, str(value))
                    elif attrb ==  'mask':
                        # TODO: invert mask
                        # TODO: broken in NVIZ
                        self._display.SetIsosurfaceMask(id, isosurfId, False, str(value))
                    elif attrb ==  'transp':
                        self._display.SetIsosurfaceTransp(id, isosurfId, map, str(value)) 
                    elif attrb ==  'shine':
                        self._display.SetIsosurfaceShine(id, isosurfId, map, str(value)) 
                    elif attrb ==  'emit':
                        self._display.SetIsosurfaceEmit(id, isosurfId, map, str(value)) 
                isosurf[attrb].pop('update')
            isosurfId +=  1
        
    def UpdateVectorProperties(self, id, data, type):
        """!Update vector layer properties
        
        @param id layer id
        @param data properties
        @param type lines/points
        """
        if type ==  'points':
            self.UpdateVectorPointsProperties(id, data[type])
        else:
            self.UpdateVectorLinesProperties(id, data[type])
        
    def UpdateVectorLinesProperties(self, id, data):
        """!Update vector line map object properties"""
        # mode
        if 'update' in data['color'] or \
                'update' in data['width'] or \
                'update' in data['mode']:
            width = data['width']['value']
            color = data['color']['value']
            if data['mode']['type'] ==  'flat':
                flat = True
                if 'surface' in data['mode']:
                    data['mode'].pop('surface')
            else:
                flat = False
            
            self._display.SetVectorLineMode(id, color,
                                            width, flat)
            
            if 'update' in data['color']:
                data['color'].pop('update')
            if 'update' in data['width']:
                data['width'].pop('update')
        
        # height
        if 'update' in data['height']:
            self._display.SetVectorLineHeight(id,
                                              data['height']['value'])
            data['height'].pop('update')
        
        # surface
        if 'surface' in data['mode'] and 'update' in data['mode']:
            for item in range(len(data['mode']['surface']['value'])):
                for type in ('raster', 'constant'):
                    sid = self.GetLayerId(type = type,
                                          name = data['mode']['surface']['value'][item])
                    if sid > -1:
                        if data['mode']['surface']['show'][item]:
                            self._display.SetVectorLineSurface(id, sid)
                        else:
                            self._display.UnsetVectorLineSurface(id, sid)
                        break
                
        if 'update' in data['mode']:
                data['mode'].pop('update')
        
    def UpdateVectorPointsProperties(self, id, data):
        """!Update vector point map object properties"""
        if 'update' in data['size'] or \
                'update' in data['width'] or \
                'update' in data['marker'] or \
                'update' in data['color']:
            ret = self._display.SetVectorPointMode(id, data['color']['value'],
                                                   data['width']['value'], float(data['size']['value']),
                                                   data['marker']['value'] + 1)
            
            error = None
            if ret ==  -1:
                error = _("Vector point layer not found (id = %d)") % id
            elif ret ==  -2:
                error = _("Unable to set data layer properties (id = %d)") % id

            if error:
                raise gcmd.GException(_("Setting data layer properties failed.\n\n%s") % error)
            
            for prop in ('size', 'width', 'marker', 'color'):
                if 'update' in data[prop]:
                    data[prop].pop('update')
        
        # height
        if 'update' in data['height']:
            self._display.SetVectorPointHeight(id,
                                               data['height']['value'])
            data['height'].pop('update')
        
        # surface
        if 'update' in data['mode']:
            for item in range(len(data['mode']['surface']['value'])):
                for type in ('raster', 'constant'):
                    sid = self.GetLayerId(type = type,
                                          name = data['mode']['surface']['value'][item])
                    if sid > -1:
                        if data['mode']['surface']['show'][item]:
                            self._display.SetVectorPointSurface(id, sid)
                        else:
                            self._display.UnsetVectorPointSurface(id, sid)   
                        break
            data['mode'].pop('update')
   
    def GetLayerNames(self, type):
        """!Return list of map layer names of given type"""
        layerName = []
        
        if type == 'constant':
            for item in self.constants:
                layerName.append(_("constant#") + str(item['constant']['object']['name']))
        else:    
            for item in self.layers:
                mapLayer = self.tree.GetPyData(item)[0]['maplayer']
                if type !=  mapLayer.GetType():
                    continue
                
                layerName.append(mapLayer.GetName())
        
        return layerName
    
    def GetLayerId(self, type, name, vsubtyp = None):
        """!Get layer object id or -1"""
        if len(name) < 1:
            return -1
        
        if type == 'constant':
            for item in self.constants:
                if _("constant#") + str(item['constant']['object']['name']) == name:
                    return item['constant']['object']['id']
                
            return self.constants
        
        for item in self.layers:
            mapLayer = self.tree.GetPyData(item)[0]['maplayer']
            if type !=  mapLayer.GetType() or \
                    name !=  mapLayer.GetName():
                continue
            
            data = self.tree.GetPyData(item)[0]['nviz']
            
            if type ==  'raster':
                return data['surface']['object']['id']
            elif type ==  'vector':
                if vsubtyp == 'vpoint':
                    return data['vector']['points']['object']['id']
                elif vsubtyp ==  'vline':
                    return data['vector']['lines']['object']['id']
            elif type ==  '3d-raster':
                return data['volume']['object']['id']
        return -1
    
    def Nviz_cmd_command(self):
        """!Generate command for nviz_cmd according to current state"""
        cmd = 'nviz_cmd '
        
        rasters = []
        vectors = []
        for item in self.layers:
            if self.tree.GetPyData(item)[0]['type'] == 'raster':
                rasters.append(item)
            elif self.tree.GetPyData(item)[0]['type'] == 'vector':
                vectors.append(item)
        if not rasters and not self.constants:
            return _("At least one raster map required")
        # elevation_map/elevation_value
        if self.constants:
            subcmd = "elevation_value="
            for constant in self.constants:
                subcmd += "%d," % constant['constant']['value']
            subcmd = subcmd.strip(', ') + ' '
            cmd += subcmd
        if rasters:
            subcmd = "elevation_map="
            for item in rasters:
                subcmd += "%s," % self.tree.GetPyData(item)[0]['maplayer'].GetName()
            subcmd = subcmd.strip(', ') + ' '
            cmd += subcmd
            #
            # draw mode
            #
            cmdMode = "mode="
            cmdFine = "resolution_fine="
            cmdCoarse = "resolution_coarse="
            cmdShading = "shading="
            cmdStyle = "style="
            cmdWire = "wire_color="
            # test -a flag
            flag_a = "-a "
            nvizDataFirst = self.tree.GetPyData(rasters[0])[0]['nviz']['surface']['draw']
            for item in rasters:
                nvizData = self.tree.GetPyData(item)[0]['nviz']['surface']['draw']
                if nvizDataFirst != nvizData:
                    flag_a = ""
            cmd += flag_a
            for item in rasters:
                nvizData = self.tree.GetPyData(item)[0]['nviz']['surface']['draw']
                
                cmdMode += "%s," % nvizData['mode']['desc']['mode']
                cmdFine += "%s," % nvizData['resolution']['fine']
                cmdCoarse += "%s," % nvizData['resolution']['coarse']
                cmdShading += "%s," % nvizData['mode']['desc']['shading']
                cmdStyle += "%s," % nvizData['mode']['desc']['style']
                cmdWire += "%s," % nvizData['wire-color']['value']
            for item in self.constants:
                cmdMode += "fine,"
                cmdFine += "%s," % item['constant']['resolution']
                cmdCoarse += "%s," % item['constant']['resolution']
                cmdShading += "gouraud,"
                cmdStyle += "surface,"
                cmdWire += "0:0:0,"
            mode = []
            for subcmd in (cmdMode, cmdFine, cmdCoarse, cmdShading, cmdStyle, cmdWire):
                if flag_a:
                    mode.append(subcmd.split(',')[0] + ' ')
                else:
                    subcmd = subcmd.strip(', ') + ' '
                    cmd += subcmd
            if flag_a:# write only meaningful possibilities
                cmd += mode[0]
                if 'fine' in mode[0]:
                    cmd += mode[1]
                elif 'coarse' in mode[0]:
                    cmd += mode[2]            
                elif 'both' in mode[0]:
                    cmd += mode[2]
                    cmd += mode[1]
                if 'flat' in mode[3]:
                    cmd += mode[3]
                if 'wire' in mode[4]:
                    cmd += mode[4]
                if 'coarse' in mode[0] or 'both' in mode[0] and 'wire' in mode[3]:
                    cmd += mode[5]
            #
            # attributes
            #
            cmdColorMap = "color_map="
            cmdColorVal = "color="
            for item in rasters:
                nvizData = self.tree.GetPyData(item)[0]['nviz']['surface']['attribute']
                if 'color' not in nvizData:
                    cmdColorMap += "%s," % self.tree.GetPyData(item)[0]['maplayer'].GetName()
                else:
                    if nvizData['color']['map']:
                        cmdColorMap += "%s," % nvizData['color']['value']
                    else:
                        cmdColorVal += "%s," % nvizData['color']['value']
                        #TODO
                        # transparency, shine, mask
            for item in self.constants:
                cmdColorVal += "%s," % item['constant']['color']
            if cmdColorMap.split("=")[1]:
                cmd += cmdColorMap.strip(', ') + ' '
            if cmdColorVal.split("=")[1]:
                cmd += cmdColorVal.strip(', ') + ' '
        # 
        # viewpoint
        subcmd  = "position=%.2f,%.2f " % (self.view['position']['x'], self.view['position']['y'])
        subcmd += "height=%d " % (self.iview['height']['value'])
        subcmd += "perspective=%d " % (self.view['persp']['value'])
        subcmd += "twist=%d " % (self.view['twist']['value'])
        subcmd += "zexag=%d " % (self.view['z-exag']['value'])
        subcmd += "focus=%d,%d,%d " % (self.iview['focus']['x'],self.iview['focus']['y'],self.iview['focus']['z'])
        cmd += subcmd
        
        # background
        subcmd  = "bgcolor=%d:%d:%d " % (self.view['background']['color'])
        if self.view['background']['color'] != (255, 255, 255):
            cmd += subcmd
        # light
        subcmd  = "light_position=%.2f,%.2f,%.2f " % (self.light['position']['x'],
                                                      self.light['position']['y'],
                                                      self.light['position']['z']/100.)
        subcmd += "light_brightness=%d " % (self.light['bright'])
        subcmd += "light_ambient=%d " % (self.light['ambient'])
        subcmd += "light_color=%d:%d:%d " % (self.light['color'])
        cmd += subcmd
        
        # fringe
        toolWindow = self.lmgr.nviz
        direction = ''
        for dir in ('nw', 'ne', 'sw', 'se'):
            if toolWindow.FindWindowById(toolWindow.win['fringe'][dir]).IsChecked():
                direction += "%s," % dir
        if direction:
            subcmd = "fringe=%s " % (direction.strip(','))
            color = toolWindow.FindWindowById(toolWindow.win['fringe']['color']).GetValue()
            subcmd += "fringe_color=%d:%d:%d " % (color[0], color[1], color[2])
            subcmd += "fringe_elevation=%d " % (toolWindow.FindWindowById(toolWindow.win['fringe']['elev']).GetValue())
            cmd += subcmd
            
        # output
        subcmd = 'output=nviz_output '
        subcmd += 'format=ppm '
        subcmd += 'size=%d,%d ' % self.GetClientSizeTuple()
        cmd += subcmd
        
        return cmd

    def SaveToFile(self, FileName, FileType, width, height):
        """!This draws the DC to a buffer that can be saved to a file.
        
        @todo fix BufferedPaintDC
        
        @param FileName file name
        @param FileType type of bitmap
        @param width image width
        @param height image height
        """
        self._display.SaveToFile(FileName, width, height)
                
        # pbuffer = wx.EmptyBitmap(max(1, self.Map.width), max(1, self.Map.height))
        # dc = wx.BufferedPaintDC(self, pbuffer)
        # dc.Clear()
        # self.SetCurrent()
        # self._display.Draw(False, -1)
        # pbuffer.SaveFile(FileName, FileType)
        # self.SwapBuffers()
        
    def GetDisplay(self):
        """!Get display instance"""
        return self._display
        
    def ZoomToMap(self):
        """!Reset view
        """
        self.lmgr.nviz.OnResetView(None)


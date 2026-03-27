// QGIS QLR Generator Helper
// This generates a proper QGIS Layer Definition file for georeferenced raster tiles

export interface TileBBox {
  bbox?: number[]; // [minLon, minLat, maxLon, maxLat]
}

export function generateQLR(tiles: TileBBox[], sessionName: string): string {
  const validTiles = tiles.filter(t => t.bbox && t.bbox.length === 4);

  if (validTiles.length === 0) {
    return '';
  }

  const layerTreeLayers = validTiles.map((_, idx) => {
    return `    <layer-tree-layer checked="Qt::Checked" expanded="1" id="tile_${idx}" name="Tile ${idx}" source="./tile_${idx}.png" providerKey="gdal">
      <customproperties/>
    </layer-tree-layer>`;
  }).join('\n');

  const mapLayers = validTiles.map((tile, idx) => {
    const [minLon, minLat, maxLon, maxLat] = tile.bbox || [0, 0, 0, 0];

    return `    <maplayer type="raster" autoRefreshEnabled="0" refreshOnNotifyEnabled="0" autoRefreshTime="0">
      <extent>
        <xmin>${minLon}</xmin>
        <ymin>${minLat}</ymin>
        <xmax>${maxLon}</xmax>
        <ymax>${maxLat}</ymax>
      </extent>
      <id>tile_${idx}</id>
      <datasource>./tile_${idx}.png</datasource>
      <keywordList><value></value></keywordList>
      <layername>Tile ${idx}</layername>
      <srs>
        <spatialrefsys>
          <wkt>GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563,AUTHORITY["EPSG","7030"]],AUTHORITY["EPSG","6326"]],PRIMEM["Greenwich",0,AUTHORITY["EPSG","8901"]],UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],AUTHORITY["EPSG","4326"]]</wkt>
          <proj4>+proj=longlat +datum=WGS84 +no_defs</proj4>
          <srsid>3452</srsid>
          <srid>4326</srid>
          <authid>EPSG:4326</authid>
          <description>WGS 84</description>
          <projectionacronym>longlat</projectionacronym>
          <ellipsoidacronym>EPSG:7030</ellipsoidacronym>
          <geographicflag>true</geographicflag>
        </spatialrefsys>
      </srs>
      <resourceMetadata>
        <identifier></identifier>
        <parentidentifier></parentidentifier>
        <language></language>
        <type></type>
        <title></title>
        <abstract></abstract>
        <fees></fees>
        <encoding></encoding>
      </resourceMetadata>
      <provider encoding="System">gdal</provider>
      <noData><noDataList bandNo="1" useSrcNoData="0"/></noData>
      <map-layer-style-manager current="default">
        <map-layer-style name="default"/>
      </map-layer-style-manager>
      <flags>
        <Identifiable>1</Identifiable>
        <Removable>1</Removable>
        <Searchable>0</Searchable>
      </flags>
      <pipe>
        <rasterrenderer type="singlebandcolordata" opacity="1" alphaBand="-1" band="1">
          <rasterTransparency/>
          <minMaxOrigin>
            <limits>None</limits>
            <extent>WholeRaster</extent>
            <statAccuracy>Estimated</statAccuracy>
            <cumulativeCutLower>0.02</cumulativeCutLower>
            <cumulativeCutUpper>0.98</cumulativeCutUpper>
            <stdDevFactor>2</stdDevFactor>
          </minMaxOrigin>
        </rasterrenderer>
        <brightnesscontrast brightness="0" contrast="0"/>
        <huesaturation colorizeGreen="128" colorizeOn="0" colorizeRed="255" colorizeBlue="128" grayscaleMode="0" saturation="0" colorizeStrength="100"/>
        <rasterresampler maxOversampling="2"/>
      </pipe>
      <blendMode>0</blendMode>
    </maplayer>`;
  }).join('\n');

  return `<!DOCTYPE qgis-layer-definition>
<qlr>
  <layer-tree-group expanded="1" checked="Qt::Checked" name="${sessionName || 'Landmap Import'}">
    <customproperties/>
${layerTreeLayers}
  </layer-tree-group>
  <maplayers>
${mapLayers}
  </maplayers>
</qlr>`;
}
